"""
DD Hunt Tracker - PDF reports with charts (reports v2).

Builds app-styled PDF reports (DD logo header, metric cards, the same charts the
app shows, tables underneath) with fpdf2 + matplotlib only (no headless browser),
so it stays light on the 512 MB Render instance.

Pure module: it never touches Supabase. app.py gathers the data (the same getters
the pages use) into a dict and calls one of the build_* functions, which return
PDF bytes ready for st.download_button.

    data = {
        "hunts": DataFrame shaped like get_all_hunts_df(),
        "surveys": DataFrame shaped like get_all_wildlife_surveys_df(),
        "farm_activities": [...], "farm_plans": {year: {location: notes}},
        "well_runs": [...], "rainfall": {year: {"vals": {m: in}, "src": {m: "auto"|"gauge"}}},
        "assets": [...], "maintenance_log": [...],
    }
"""
from __future__ import annotations

import gc
import io
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from fpdf import FPDF, XPos, YPos
from fpdf.fonts import FontFace

# matplotlib is imported lazily (first chart only, Agg backend) to keep app memory low.
plt = None
mdates = None


def _mpl():
    global plt, mdates
    if plt is None:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.dates as _md
        import matplotlib.pyplot as _plt
        plt, mdates = _plt, _md

BASE_DIR = Path(__file__).parent
LOGO_PATH = BASE_DIR / "dd_logo.png"
FARM_MAPS_DIR = BASE_DIR / "farm_maps"
CLUB_TZ = ZoneInfo("America/Chicago")

# Mirrors app.py constants (kept here so this module has no Streamlit import).
SPECIES = ["Mallard", "Gadwall", "Teal", "Pintail", "Wood Duck", "Widgeon",
           "Shoveler", "Canvasback", "Redhead", "Divers", "Geese"]
SPECIES_COLS = {sp: sp.lower().replace(" ", "_") + "_count" for sp in SPECIES}
WELLS = ["Main", "South Block"]
RAIN_MONTHS = {7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct"}
FARM_ACTIVITIES = ["Planting", "Coffee Bean Spraying", "Brushing", "Farm Disking"]
FARM_PLAN_LOCATIONS = ["Pool 1", "Money", "Willow", "Refuge", "South Block", "Bar Pit",
                       "Black Bayou", "Bayou Plot", "Bar Plot", "Sunflower Patch"]
PUMPING_STRATEGY = [
    "Once a field has reached the Top Board, you should consider it full unless you can stay overnight.",
    "Ideally, members should go every three days to check.",
    "Always pump into Pool 1 or Money. That gives you more options, as the well will produce more water than can go through the gate.",
    "Make sure all inner gates have all boards removed.",
    "Remember South Block does not need more than 12 days.",
]

# ---- DD palette (app sidebar / login page + logo) ----
GREEN = "#1B3A2A"
CREAM = "#F7F4EA"
BROWN = "#84603A"
SLATE = "#4a5c52"
BORDER = "#c5c0b0"
RUST = "#8B2E2E"
SERIES = [GREEN, BROWN, "#7A9A6B", "#C9A66B", SLATE, "#A3A86B", RUST, "#D9CBA3",
          "#2F6B4F", "#B5835A", "#5E7F8C"]


def _rgb(hex_color: str):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


_LATIN = {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
          "\u2014": "-", "\u2026": "...", "\u2022": "-", "\u00a0": " ", "\u2192": "->"}


def clean(v) -> str:
    """Text safe for the core Helvetica font (latin-1)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    s = str(v)
    for a, b in _LATIN.items():
        s = s.replace(a, b)
    return s.encode("latin-1", "ignore").decode("latin-1").strip()


# ---------------- helpers mirrored from app.py ----------------
def parse_hunters(raw) -> list[str]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return []
    if isinstance(raw, list):
        return [str(h).strip() for h in raw if str(h).strip()]
    text = str(raw).replace("\r\n", "\n").replace("\r", "\n")
    parts = text.split("\n") if "\n" in text else (text.split(" | ") if " | " in text else [text])
    return [p.strip() for p in parts if p.strip()]


def season_birds_per_hunter(df: pd.DataFrame):
    if df is None or df.empty:
        return None
    slots = int(df["hunters"].apply(lambda h: len(parse_hunters(h))).sum())
    return round(float(df["daily_total"].fillna(0).sum()) / slots, 2) if slots else None


def _run_bounds(r: dict, now=None):
    if not r.get("on_date"):
        return None

    def _dt(d, t):
        return datetime.fromisoformat(f"{str(d)[:10]}T{str(t)[:5] if t else '00:00'}")
    try:
        start = _dt(r["on_date"], r.get("on_time"))
        if r.get("off_date"):
            return start, _dt(r["off_date"], r.get("off_time")), False
        return start, (now or datetime.now(CLUB_TZ).replace(tzinfo=None)), True
    except Exception:
        return None


def _run_hours(r: dict, since=None):
    b = _run_bounds(r)
    if not b:
        return None
    start, end, _ = b
    if since and start < since:
        start = since
    return max(0.0, (end - start).total_seconds() / 3600.0)


def _run_days(r: dict):
    try:
        if r.get("on_date") and r.get("off_date"):
            return (date.fromisoformat(str(r["off_date"])[:10]) - date.fromisoformat(str(r["on_date"])[:10])).days
        if r.get("on_date"):
            return (datetime.now(CLUB_TZ).date() - date.fromisoformat(str(r["on_date"])[:10])).days
    except Exception:
        pass
    return None


def _run_year(r: dict):
    for k in ("on_date", "off_date", "created_at"):
        if r.get(k):
            try:
                return int(str(r[k])[:4])
            except ValueError:
                pass
    return None


def _num(v):
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def hunts_df_from_rows(rows: list[dict]) -> pd.DataFrame:
    """Supabase hunts rows -> the app's get_all_hunts_df() shape (used by the sample script)."""
    out = []
    for r in rows or []:
        d = {"id": r.get("id"), "date": r.get("date"), "location": r.get("location"), "wind": r.get("wind"),
             "temp_high": r.get("high_temp"), "temp_low": r.get("low_temp"), "river_level": r.get("river_level"),
             "rainfall": r.get("rainfall") or 0, "notes": r.get("notes"), "season": r.get("season"),
             "hunters": " | ".join(parse_hunters(r.get("hunters")))}
        for sp in SPECIES:
            d[SPECIES_COLS[sp]] = int(r.get(sp.lower().replace(" ", "_")) or 0)
        d["daily_total"] = sum(d[c] for c in SPECIES_COLS.values())
        out.append(d)
    df = pd.DataFrame(out)
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df.sort_values(["date", "id"], ascending=False).reset_index(drop=True)
    return df


# ---------------- charts (matplotlib -> PNG bytes) ----------------
def _style(ax, title=None):
    ax.set_facecolor("white")
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(BORDER)
    ax.tick_params(colors=SLATE, labelsize=8)
    ax.yaxis.grid(True, color="#ebe6d6", linewidth=0.8)
    ax.set_axisbelow(True)
    if title:
        ax.set_title(title, loc="left", fontsize=10.5, color=GREEN, fontweight="bold", pad=8)


def _png(fig) -> io.BytesIO:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight", facecolor="white")
    fig.clf()
    plt.close(fig)
    gc.collect()
    buf.seek(0)
    return buf


def _fig(w=7.2, h=3.0):
    _mpl()
    fig, ax = plt.subplots(figsize=(w, h))
    fig.patch.set_facecolor("white")
    return fig, ax


def chart_pie(totals: dict, title: str, w=3.6, h=3.0):
    items = sorted(((k, v) for k, v in totals.items() if v), key=lambda kv: -kv[1])
    fig, ax = _fig(w, h)
    vals = [v for _, v in items]
    wedges, _, autotexts = ax.pie(
        vals, labels=None, colors=SERIES[:len(items)], startangle=90, counterclock=False,
        autopct=lambda p: f"{p:.0f}%" if p >= 4 else "", pctdistance=0.78,
        wedgeprops={"width": 0.45, "edgecolor": "white", "linewidth": 1.5})
    for t in autotexts:
        t.set_color("white"); t.set_fontsize(8); t.set_fontweight("bold")
    ax.text(0, 0, f"{sum(vals)}\nbirds", ha="center", va="center", fontsize=11, color=GREEN, fontweight="bold")
    ax.legend(wedges, [f"{k} ({v})" for k, v in items], loc="center left", bbox_to_anchor=(1.0, 0.5),
              frameon=False, fontsize=8, labelcolor=SLATE)
    ax.set_title(title, loc="left", fontsize=10.5, color=GREEN, fontweight="bold")
    ax.axis("equal")
    return _png(fig)


def chart_barh(labels, values, title, color=GREEN, w=3.6, h=None, fmt="{:g}", xlabel=None):
    h = h or max(1.6, 0.32 * len(labels) + 0.9)
    fig, ax = _fig(w, h)
    labels, values = list(labels)[::-1], list(values)[::-1]
    bars = ax.barh([clean(l) for l in labels], values, color=color, height=0.62 if len(labels) > 2 else 0.4)
    _style(ax, title)
    ax.xaxis.grid(True, color="#ebe6d6"); ax.yaxis.grid(False)
    m = max(values) if values else 1
    for b, v in zip(bars, values):
        ax.text(b.get_width() + m * 0.02, b.get_y() + b.get_height() / 2, fmt.format(v),
                va="center", fontsize=8, color=SLATE)
    ax.set_xlim(0, m * 1.18 if m else 1)
    if xlabel:
        ax.set_xlabel(xlabel, fontsize=8, color=SLATE)
    return _png(fig)


def chart_bar(labels, values, title, color=GREEN, w=3.6, h=2.6, ylabel=None, fmt="{:g}"):
    fig, ax = _fig(w, h)
    bars = ax.bar([clean(l) for l in labels], values, color=color, width=0.6)
    if len(labels) < 4:
        ax.set_xlim(-0.5 - (4 - len(labels)) * 0.5, len(labels) - 0.5 + (4 - len(labels)) * 0.5)
    _style(ax, title)
    m = max(values) if len(values) else 1
    for b, v in zip(bars, values):
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + m * 0.02, fmt.format(v),
                ha="center", va="bottom", fontsize=8, color=SLATE, fontweight="bold")
    ax.set_ylim(0, m * 1.2 if m else 1)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8, color=SLATE)
    plt.setp(ax.get_xticklabels(), fontsize=8)
    return _png(fig)


def chart_line(dates, values, title, labels=None, w=7.2, h=2.6):
    fig, ax = _fig(w, h)
    ax.plot(dates, values, color=GREEN, marker="o", markersize=7, linewidth=2.2,
            markerfacecolor=BROWN, markeredgecolor="white", markeredgewidth=1.2)
    ax.fill_between(dates, values, color=GREEN, alpha=0.07)
    _style(ax, title)
    m = max(values) if values else 1
    for i, (d, v) in enumerate(zip(dates, values)):
        lab = f"{v}" + (f"  {clean(labels[i])}" if labels else "")
        ax.annotate(lab, (d, v), textcoords="offset points", xytext=(0, 9), ha="center", fontsize=8, color=SLATE)
    ax.set_ylim(0, m * 1.3 if m else 1)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    if len(dates) <= 10:
        ax.set_xticks(list(dates))
    if len(dates) == 1 or (len(dates) and (max(dates) - min(dates)).days < 4):
        pad = pd.Timedelta(days=1)
        ax.set_xlim(min(dates) - pad, max(dates) + pad)
    ax.set_ylabel("Ducks", fontsize=8, color=SLATE)
    return _png(fig)


def chart_stacked(categories, series: dict, title, w=7.2, h=2.8):
    fig, ax = _fig(w, h)
    bottom = [0] * len(categories)
    for i, (name, vals) in enumerate(series.items()):
        ax.bar([clean(c) for c in categories], vals, bottom=bottom, color=SERIES[i % len(SERIES)],
               label=name, width=0.55, edgecolor="white", linewidth=0.8)
        bottom = [b + v for b, v in zip(bottom, vals)]
    if len(categories) < 4:
        ax.set_xlim(-0.5 - (4 - len(categories)) * 0.4, len(categories) - 0.5 + (4 - len(categories)) * 0.4)
    for x, t in enumerate(bottom):
        ax.text(x, t + max(bottom) * 0.02, f"{t:g}", ha="center", va="bottom", fontsize=8, color=SLATE, fontweight="bold")
    _style(ax, title)
    ax.set_ylim(0, max(bottom) * 1.2 if bottom and max(bottom) else 1)
    ax.legend(loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False, fontsize=8, labelcolor=SLATE)
    return _png(fig)


def chart_grouped(categories, series: dict, title, w=7.2, h=2.6, ylabel=None, fmt="{:.2f}"):
    import numpy as np
    fig, ax = _fig(w, h)
    n = max(len(series), 1)
    x = np.arange(len(categories))
    width = 0.8 / n
    for i, (name, vals) in enumerate(series.items()):
        bars = ax.bar(x + (i - (n - 1) / 2) * width, [v or 0 for v in vals], width * 0.92,
                      color=SERIES[i % len(SERIES)], label=str(name))
        for b, v in zip(bars, vals):
            if v:
                ax.text(b.get_x() + b.get_width() / 2, b.get_height(), fmt.format(v), ha="center",
                        va="bottom", fontsize=6.5, color=SLATE)
    ax.set_xticks(x, [clean(c) for c in categories])
    _style(ax, title)
    ax.legend(frameon=False, fontsize=8, labelcolor=SLATE, ncol=min(n, 4), loc="upper left")
    ax.set_ylim(0, (ax.get_ylim()[1]) * 1.18)
    if ylabel:
        ax.set_ylabel(ylabel, fontsize=8, color=SLATE)
    return _png(fig)


def chart_timeline(rows: list[tuple], title, w=7.2, h=None, xfmt="%b %d"):
    """rows: (label, start_dt, end_dt, color, caption)"""
    h = h or max(1.6, 0.45 * len(rows) + 1.0)
    fig, ax = _fig(w, h)
    for i, (lab, s, e, col, cap) in enumerate(rows):
        width = max((e - s).total_seconds() / 86400.0, 0.6)
        ax.barh(i, width, left=mdates.date2num(s), color=col, height=0.5)
        if cap:
            ax.text(mdates.date2num(s) + width / 2, i, clean(cap), ha="center", va="center", fontsize=7.5,
                    color="white", fontweight="bold")
    ax.set_yticks(range(len(rows)), [clean(r[0]) for r in rows])
    ax.invert_yaxis()
    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter(xfmt))
    _style(ax, title)
    ax.yaxis.grid(False); ax.xaxis.grid(True, color="#ebe6d6")
    return _png(fig)


def chart_events(events: list[tuple], categories: list[str], title, w=7.2, h=None):
    """events: (category, date, label) -> dot timeline per category."""
    h = h or max(1.6, 0.42 * len(categories) + 1.0)
    fig, ax = _fig(w, h)
    for i, cat in enumerate(categories):
        ax.axhline(i, color="#ebe6d6", linewidth=1, zorder=0)
        pts = [(d, lab) for c, d, lab in events if c == cat]
        for d, lab in pts:
            ax.scatter([d], [i], s=70, color=SERIES[i % len(SERIES)], zorder=3, edgecolor="white")
            ax.annotate(clean(lab), (d, i), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=7, color=SLATE)
    ax.set_yticks(range(len(categories)), categories)
    ax.set_ylim(len(categories) - 0.4, -0.6)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    ds = [d for _, d, _ in events]
    if ds:
        pad = pd.Timedelta(days=12)
        ax.set_xlim(min(ds) - pad, max(ds) + pad)
    _style(ax, title)
    ax.yaxis.grid(False)
    return _png(fig)


# ---------------- PDF layout ----------------
class DDReport(FPDF):
    def __init__(self, title: str, subtitle: str = ""):
        super().__init__(orientation="P", unit="mm", format="Letter")
        self.report_title = clean(title)
        self.report_subtitle = clean(subtitle)
        self.generated = datetime.now(CLUB_TZ).strftime("%b %d, %Y %I:%M %p CT").replace(" 0", " ")
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(12, 30, 12)
        self.alias_nb_pages()
        self.set_title(self.report_title)
        self.set_author("DD Hunt Tracker")

    # header band like the app: cream with DD logo + green rule
    def header(self):
        w = self.w
        self.set_fill_color(*_rgb(CREAM))
        self.rect(0, 0, w, 24, style="F")
        if LOGO_PATH.exists():
            self.image(str(LOGO_PATH), x=10, y=2.5, h=19)
        self.set_xy(32, 6)
        self.set_text_color(*_rgb(GREEN))
        self.set_font("Helvetica", "B", 15)
        self.cell(w - 44, 7, self.report_title)
        self.set_xy(32, 13.5)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(*_rgb(SLATE))
        self.cell(w - 44, 5, self.report_subtitle)
        self.set_font("Helvetica", "", 8)
        self.set_xy(w - 72, 6)
        self.cell(60, 5, "DD Hunt Tracker", align="R")
        self.set_xy(w - 72, 11)
        self.cell(60, 5, f"Generated {self.generated}", align="R")
        self.set_fill_color(*_rgb(GREEN))
        self.rect(0, 24, w, 1.6, style="F")
        self.set_fill_color(*_rgb(BROWN))
        self.rect(0, 25.6, w, 0.5, style="F")
        self.set_y(30)

    def footer(self):
        self.set_y(-11)
        self.set_draw_color(*_rgb(BORDER))
        self.line(12, self.get_y() - 1, self.w - 12, self.get_y() - 1)
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(*_rgb(SLATE))
        self.cell(0, 5, "DD Hunt Tracker  -  Private Club Use Only  -  dd-hunt-tracker.onrender.com")
        self.set_x(-50)
        self.cell(38, 5, f"Page {self.page_no()} of {{nb}}", align="R")

    # ---- building blocks ----
    @property
    def cw(self):
        return self.w - self.l_margin - self.r_margin

    def ensure(self, h):
        if self.get_y() + h > self.page_break_trigger:
            self.add_page(orientation=self.cur_orientation)

    def section(self, title: str, caption: str = "", level: int = 0, new_page: bool = False, orientation="P",
                need: float | None = None):
        if new_page:
            self.add_page(orientation=orientation)
        else:
            self.ensure(need or (90 if level == 0 else 30))
        self.start_section(clean(title), level=level)
        self.set_text_color(*_rgb(GREEN))
        self.set_font("Helvetica", "B", 15 if level == 0 else 12)
        self.cell(0, 8 if level == 0 else 7, clean(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(*_rgb(BROWN))
        self.set_line_width(0.6 if level == 0 else 0.3)
        self.line(self.l_margin, self.get_y(), self.l_margin + (40 if level == 0 else 25), self.get_y())
        self.set_line_width(0.2)
        self.ln(1.5)
        if caption:
            self.note(caption)
        self.ln(1)

    def note(self, text: str, italic=True, size=8.5, color=SLATE):
        self.set_font("Helvetica", "I" if italic else "", size)
        self.set_text_color(*_rgb(color))
        self.multi_cell(0, 4.2, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def para(self, text: str, size=9.5):
        self.set_font("Helvetica", "", size)
        self.set_text_color(30, 30, 30)
        self.multi_cell(0, 4.8, clean(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def cards(self, items: list[tuple], h=20):
        """st.metric look: label (small), big value, optional caption."""
        n = len(items)
        gap = 3
        w = (self.cw - gap * (n - 1)) / n
        self.ensure(h + 4)
        y = self.get_y()
        for i, it in enumerate(items):
            label, value = it[0], it[1]
            sub = it[2] if len(it) > 2 else ""
            x = self.l_margin + i * (w + gap)
            self.set_fill_color(*_rgb(CREAM))
            self.set_draw_color(*_rgb(BORDER))
            self.rect(x, y, w, h, style="DF", round_corners=True, corner_radius=2)
            self.set_fill_color(*_rgb(GREEN))
            self.rect(x, y + 2, 1.2, h - 4, style="F")
            self.set_xy(x + 4, y + 2.5)
            self.set_font("Helvetica", "", 7.5)
            self.set_text_color(*_rgb(SLATE))
            self.cell(w - 6, 4, clean(label))
            self.set_xy(x + 4, y + 7)
            fs = 17 if len(clean(value)) <= 9 else 12
            self.set_font("Helvetica", "B", fs)
            self.set_text_color(*_rgb(GREEN))
            self.cell(w - 6, 8, clean(value))
            if sub:
                self.set_xy(x + 4, y + h - 5.5)
                self.set_font("Helvetica", "", 6.8)
                self.set_text_color(*_rgb(BROWN))
                self.cell(w - 6, 4, clean(sub))
        self.set_y(y + h + 4)

    def chart(self, png: io.BytesIO, w=None, x=None):
        from PIL import Image
        w = w or self.cw
        png.seek(0)
        iw, ih = Image.open(png).size
        h = w * ih / iw
        png.seek(0)
        self.ensure(h + 3)
        self.image(png, x=x if x is not None else self.l_margin, y=self.get_y(), w=w)
        self.set_y(self.get_y() + h + 3)

    def chart_pair(self, a: io.BytesIO, b: io.BytesIO):
        from PIL import Image
        gap = 4
        w = (self.cw - gap) / 2
        hs = []
        for p in (a, b):
            p.seek(0)
            iw, ih = Image.open(p).size
            hs.append(w * ih / iw)
            p.seek(0)
        self.ensure(max(hs) + 3)
        y = self.get_y()
        self.image(a, x=self.l_margin, y=y, w=w)
        self.image(b, x=self.l_margin + w + gap, y=y, w=w)
        self.set_y(y + max(hs) + 3)

    def table(self, headers: list[str], rows: list[list], widths=None, align=None, size=7.8, line_h=5.2):
        if not rows:
            self.note("No rows.")
            return
        self.set_font("Helvetica", "", size)
        self.set_text_color(25, 25, 25)
        self.set_draw_color(*_rgb(BORDER))
        self.set_fill_color(255, 255, 255)  # table() inherits the current fill as its base style
        self.ensure(line_h * 3)
        head = FontFace(emphasis="BOLD", color=_rgb(CREAM), fill_color=_rgb(GREEN))
        with super().table(col_widths=widths or None, text_align=align or "LEFT", headings_style=head,
                           line_height=line_h, cell_fill_color=_rgb(CREAM), cell_fill_mode="ROWS",
                           borders_layout="HORIZONTAL_LINES", padding=1.2, width=self.cw,
                           first_row_as_headings=True, repeat_headings=1) as t:
            r = t.row()
            for hd in headers:
                r.cell(clean(hd))
            for row in rows:
                r = t.row()
                for v in row:
                    r.cell(clean(_fmt(v)))
        self.ln(3)

    def callout(self, title: str, bullets: list[str]):
        self.set_font("Helvetica", "", 9)
        lines = sum(max(1, int(self.get_string_width(clean(b)) / (self.cw - 14)) + 1) for b in bullets)
        h = 10 + lines * 4.8
        self.ensure(h + 3)
        y = self.get_y()
        self.set_fill_color(*_rgb(CREAM))
        self.set_draw_color(*_rgb(BORDER))
        self.rect(self.l_margin, y, self.cw, h, style="DF", round_corners=True, corner_radius=2)
        self.set_xy(self.l_margin + 4, y + 2.5)
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*_rgb(GREEN))
        self.cell(0, 5, clean(title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(30, 30, 30)
        for b in bullets:
            self.set_x(self.l_margin + 4)
            self.multi_cell(self.cw - 8, 4.8, "-  " + clean(b), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_y(y + h + 3)

    def photo(self, path: Path, caption: str, max_h=110):
        from PIL import Image
        if not path.exists():
            return False
        im = Image.open(path).convert("RGB")
        if im.width > 1600:
            im = im.resize((1600, int(im.height * 1600 / im.width)))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=82)
        buf.seek(0)
        w = self.cw
        h = w * im.height / im.width
        if h > max_h:
            h = max_h
            w = h * im.width / im.height
        self.ensure(h + 8)
        self.image(buf, x=self.l_margin + (self.cw - w) / 2, y=self.get_y(), w=w, h=h)
        self.set_y(self.get_y() + h + 1)
        self.note(caption)
        self.ln(2)
        return True


def _fmt(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if isinstance(v, float):
        return f"{v:,.2f}".rstrip("0").rstrip(".") if v != int(v) else f"{int(v):,}"
    if isinstance(v, (pd.Timestamp, datetime)):
        return v.strftime("%Y-%m-%d")
    return str(v)


def _toc(pdf: DDReport, outline):
    pdf.set_x(pdf.l_margin)
    pdf.set_text_color(*_rgb(GREEN))
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Contents", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(1)
    if len(outline) > 44:
        outline = [s for s in outline if s.level == 0]
    for s in outline:
        pdf.set_font("Helvetica", "B" if s.level == 0 else "", 10 if s.level == 0 else 9)
        pdf.set_text_color(*_rgb(GREEN if s.level == 0 else SLATE))
        indent = 4 + 6 * s.level
        pdf.set_x(pdf.l_margin + indent)
        name = clean(s.name)
        pdf.cell(pdf.cw - indent - 12, 5.6 if s.level == 0 else 5, name, link=pdf.add_link(page=s.page_number))
        pdf.cell(12, 5.6 if s.level == 0 else 5, str(s.page_number), align="R", new_x=XPos.LMARGIN, new_y=YPos.NEXT)


# ---------------- data rollups ----------------
def species_totals(df: pd.DataFrame) -> dict:
    return {sp: int(df[SPECIES_COLS[sp]].sum()) for sp in SPECIES if SPECIES_COLS[sp] in df.columns}


def hunt_stats(df: pd.DataFrame) -> dict:
    n = len(df)
    slots = int(df["hunters"].apply(lambda h: len(parse_hunters(h))).sum()) if n else 0
    return {
        "total": int(df["daily_total"].sum()) if n else 0,
        "hunts": n,
        "avg": round(float(df["daily_total"].mean()), 1) if n else 0,
        "best": int(df["daily_total"].max()) if n else 0,
        "bph": season_birds_per_hunter(df),
        "slots": slots,
    }


def hunter_pivot(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        for name in parse_hunters(r.get("hunters")):
            rows.append({"Hunter": name, "Ducks": int(r.get("daily_total") or 0), "Hunts": 1})
    if not rows:
        return pd.DataFrame(columns=["Hunter", "Hunts", "Ducks", "Birds / Hunter-Day"])
    p = pd.DataFrame(rows).groupby("Hunter", as_index=False).agg(Hunts=("Hunts", "sum"), Ducks=("Ducks", "sum"))
    p["Birds / Hunter-Day"] = (p["Ducks"] / p["Hunts"]).round(2)
    return p.sort_values(["Ducks", "Hunter"], ascending=[False, True])


def group_pivot(df: pd.DataFrame, key: str, label: str) -> pd.DataFrame:
    t = df.copy()
    t["_g"] = t[key].fillna("-")
    t["_n"] = t["hunters"].apply(lambda h: len(parse_hunters(h)))
    g = t.groupby("_g", as_index=False).agg(Hunts=("date", "count"), Ducks=("daily_total", "sum"), Slots=("_n", "sum"))
    g = g.rename(columns={"_g": label, "Slots": "Hunter Slots"})
    g["Birds / Hunter"] = g.apply(lambda r: round(r["Ducks"] / r["Hunter Slots"], 2) if r["Hunter Slots"] else None, axis=1)
    return g.sort_values("Ducks", ascending=False)


def weekly_totals(df: pd.DataFrame) -> pd.DataFrame:
    d = df.sort_values("date").copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d["week_start"] = d["date"] - pd.to_timedelta(d["date"].dt.dayofweek, unit="D")
    w = d.groupby("week_start")["daily_total"].sum().reset_index().sort_values("week_start")
    w["Week"] = w["week_start"].dt.strftime("Week of %b %d")
    return w


def _top_species(row) -> str:
    best, cnt = "", 0
    for sp in SPECIES:
        v = row.get(SPECIES_COLS[sp], 0) or 0
        if v > cnt:
            best, cnt = sp, v
    return f"{best} {int(cnt)}" if cnt else "-"


def _short_date(d) -> str:
    d = pd.to_datetime(d)
    return f"{d.strftime('%a %b')} {d.day}"


# ---------------- report sections (shared by every report) ----------------
def sec_dashboard(pdf: DDReport, df: pd.DataFrame, period: str):
    st = hunt_stats(df)
    pdf.section("Dashboard", f"Season: {period}. Same cards and charts as the app's Dashboard.")
    pdf.cards([("Total Ducks", f"{st['total']:,}"), ("Hunting Days", st["hunts"]),
               ("Avg Daily Bag", st["avg"]), ("Best Day", st["best"]),
               ("Hunter Avg", f"{st['bph']:.2f}" if st["bph"] is not None else "-", f"{st['slots']} hunter-slots")])
    tot = species_totals(df)
    nz = {k: v for k, v in tot.items() if v}
    if nz:
        items = sorted(nz.items(), key=lambda kv: -kv[1])
        pdf.chart_pair(chart_pie(nz, "Harvest by Species"),
                       chart_barh([k for k, _ in items], [v for _, v in items], "Birds by Species", color=BROWN))
    pdf.section("Recent Activity", level=1)
    recent = df.head(5)
    pdf.table(["Date", "Location", "Ducks", "Hunters", "Top Species"],
              [[_short_date(r["date"]), r.get("location"), int(r["daily_total"]),
                ", ".join(parse_hunters(r.get("hunters"))), _top_species(r)] for _, r in recent.iterrows()],
              widths=(18, 22, 12, 100, 28), align=("LEFT", "LEFT", "CENTER", "LEFT", "LEFT"))


def sec_analytics(pdf: DDReport, df: pd.DataFrame):
    pdf.section("Season Analytics", "Daily Bag Trend, Top 5 Days and Weekly Totals, as on the Season Analytics page.", need=110)
    d = df.sort_values("date")
    pdf.chart(chart_line(list(pd.to_datetime(d["date"])), [int(v) for v in d["daily_total"]],
                         "Ducks per Hunt Day", labels=list(d["location"].fillna(""))))
    w = weekly_totals(df)
    by_hunt = d.copy()
    by_hunt["lab"] = by_hunt["date"].apply(_short_date)
    bph = [round(r["daily_total"] / max(len(parse_hunters(r["hunters"])), 1), 2) for _, r in by_hunt.iterrows()]
    pdf.chart_pair(chart_bar(list(w["Week"]), [int(v) for v in w["daily_total"]], "Ducks by Week"),
                   chart_bar(list(by_hunt["lab"]), bph, "Birds per Hunter, by Hunt", color=BROWN, fmt="{:.2f}"))
    pdf.section("Top 5 Days", level=1)
    top = df.nlargest(5, "daily_total")
    pdf.table(["Date", "Location", "Ducks", "Hunters"],
              [[_short_date(r["date"]), r.get("location"), int(r["daily_total"]),
                ", ".join(parse_hunters(r.get("hunters")))] for _, r in top.iterrows()],
              widths=(22, 26, 14, 118), align=("LEFT", "LEFT", "CENTER", "LEFT"))


def sec_breakdowns(pdf: DDReport, df: pd.DataFrame):
    pdf.section("Harvest Breakdown", "Species by hunt, birds by blind and by hunter.")
    d = df.sort_values("date")
    used = [sp for sp in SPECIES if int(d[SPECIES_COLS[sp]].sum())]
    cats = [f"{_short_date(r['date'])}\n{clean(r['location'])}" for _, r in d.iterrows()]
    pdf.chart(chart_stacked(cats, {sp: [int(v) for v in d[SPECIES_COLS[sp]]] for sp in used},
                            "Species Mix by Hunt", h=2.6))
    loc = group_pivot(df, "location", "Location")
    hp = hunter_pivot(df)
    pdf.chart_pair(chart_bar(list(loc["Location"]), [int(v) for v in loc["Ducks"]], "Ducks by Blind / Location"),
                   chart_barh(list(hp["Hunter"]), [int(v) for v in hp["Ducks"]],
                              "Ducks on Hunts Attended, by Hunter", color=BROWN))
    pdf.note("Hunter totals use the app's club-style attribution: each hunter is credited the full day's bag for "
             "every hunt they were on. Names are shown exactly as entered on each report.")


def sec_hunt_log(pdf: DDReport, df: pd.DataFrame, new_page=True):
    pdf.section("Hunt Log", "Every hunt in the period, with conditions and species counts (species with no birds are left off).",
                new_page=new_page, orientation="L")
    d = df.sort_values("date", ascending=False)
    used = [sp for sp in SPECIES if int(d[SPECIES_COLS[sp]].sum())]
    headers = ["Date", "Location", "Hunters", "#", "Ducks", "B/H", "Wind", "Hi", "Lo"] + used
    rows = []
    for _, r in d.iterrows():
        hs = parse_hunters(r.get("hunters"))
        rows.append([pd.to_datetime(r["date"]).strftime("%a %m/%d/%y"), r.get("location"), ", ".join(hs), len(hs),
                     int(r["daily_total"]), round(r["daily_total"] / len(hs), 2) if hs else "",
                     r.get("wind"), r.get("temp_high"), r.get("temp_low")] +
                    [int(r[SPECIES_COLS[sp]]) for sp in used])
    sp_w = 14
    fixed = [24, 22, 0, 7, 11, 10, 16, 8, 8]
    fixed[2] = pdf.cw - sum(fixed) - sp_w * len(used)
    pdf.table(headers, rows, widths=fixed + [sp_w] * len(used),
              align=["LEFT", "LEFT", "LEFT"] + ["CENTER"] * (len(headers) - 3), size=7.5)
    pdf.section("Hunt Details & Notes", level=1)
    for _, r in d.iterrows():
        pdf.set_font("Helvetica", "B", 9)
        pdf.set_text_color(*_rgb(GREEN))
        pdf.ensure(16)
        pdf.cell(0, 5, clean(f"Hunt #{r.get('id')}  -  {pd.to_datetime(r['date']).strftime('%A, %b %d, %Y')}  -  "
                             f"{r.get('location')}  -  {int(r['daily_total'])} birds  ({r.get('season')})"),
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        sp = ", ".join(f"{s} {int(r[SPECIES_COLS[s]])}" for s in SPECIES if r[SPECIES_COLS[s]])
        pdf.note(f"Weather: {r.get('wind') or '-'} | High {r.get('temp_high')}F / Low {r.get('temp_low')}F | "
                 f"River: {r.get('river_level') or '-'} | Rain: {r.get('rainfall') or 0}\"    Species: {sp}", italic=False)
        pdf.note(f"Hunters: {', '.join(parse_hunters(r.get('hunters'))) or '-'}", italic=False)
        if clean(r.get("notes")):
            pdf.note(f"Notes: {r.get('notes')}")
        pdf.ln(1.5)


def sec_pivots(pdf: DDReport, df: pd.DataFrame):
    pdf.section("Pivot Summaries", "The Reports page pivot, rolled up all three ways.", new_page=True)
    st = hunt_stats(df)
    pdf.cards([("Ducks in Selected Period", st["total"]), ("Hunts", st["hunts"]),
               ("Hunter Avg", f"{st['bph']:.2f}" if st["bph"] is not None else "-")], h=18)
    for key, label in (("location", "Location"), ("season", "Season")):
        g = group_pivot(df, key, label)
        pdf.section(f"By {label}", level=1)
        pdf.table([label, "Hunts", "Ducks", "Hunter Slots", "Birds / Hunter"],
                  g[[label, "Hunts", "Ducks", "Hunter Slots", "Birds / Hunter"]].values.tolist(),
                  align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER"))
    hp = hunter_pivot(df)
    pdf.section("By Hunter", level=1)
    pdf.table(["Hunter", "Hunts", "Ducks", "Birds / Hunter-Day"], hp.values.tolist(),
              align=("LEFT", "CENTER", "CENTER", "CENTER"))


def sec_species_table(pdf: DDReport, df: pd.DataFrame):
    tot = species_totals(df)
    total = sum(tot.values()) or 1
    pdf.section("Species Breakdown", level=1)
    rows = [[sp, c, f"{100 * c / total:.1f}%"] for sp, c in sorted(tot.items(), key=lambda kv: -kv[1]) if c]
    pdf.table(["Species", "Birds", "Share"], rows, widths=(60, 30, 30), align=("LEFT", "CENTER", "CENTER"))


def sec_wildlife(pdf: DDReport, sdf: pd.DataFrame, new_page=True):
    pdf.section("Wildlife Survey", "Scouting log - presence only, no counts (separate from harvest reports).", new_page=new_page)
    sdf = sdf.copy()
    sdf["_d"] = pd.to_datetime(sdf["survey_date"], errors="coerce")
    sp_lists = sdf["species_seen"].fillna("").apply(lambda s: [x.strip() for x in s.split(",") if x.strip()])
    freq = {}
    for lst in sp_lists:
        for s in lst:
            freq[s] = freq.get(s, 0) + 1
    last = sdf["_d"].max()
    pdf.cards([("Surveys", len(sdf)), ("Locations scouted", sdf["location"].nunique()),
               ("Species tagged", len(freq)), ("Last survey", last.strftime("%b %d") if pd.notna(last) else "-")])
    loc = sdf["location"].value_counts()
    fi = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    a = chart_barh(list(loc.index), [int(v) for v in loc.values], "Surveys by Location")
    b = chart_barh([k for k, _ in fi], [v for _, v in fi], "Species Seen (surveys reporting)", color=BROWN) if fi else None
    if b:
        pdf.chart_pair(a, b)
    else:
        pdf.chart(a, w=pdf.cw / 2)
    mon = sdf.dropna(subset=["_d"]).groupby(sdf["_d"].dt.to_period("M")).size()
    if len(mon):
        events = [(r["location"], r["_d"], ", ".join(sp_lists[i]) or "-") for i, r in sdf.dropna(subset=["_d"]).iterrows()]
        cats = list(dict.fromkeys(sdf.sort_values("_d")["location"]))
        pdf.chart(chart_events(events, cats, "Survey Timeline (what was tagged)"))
    pdf.section("Survey Log", level=1)
    d = sdf.sort_values(["_d", "id"], ascending=False)
    pdf.table(["Date", "Location", "Seen", "Who", "Notes"],
              [[r["_d"].strftime("%Y-%m-%d") if pd.notna(r["_d"]) else "", r["location"], r["species_seen"],
                r["observers"], r["notes"]] for _, r in d.iterrows()],
              widths=(20, 22, 34, 28, 88))


def sec_farm(pdf: DDReport, acts: list, plans: dict, year: int, new_page=True):
    pdf.section("Farm Plans", "Field work log, yearly plot notes, and the farm maps.", new_page=new_page)
    pdf.cards([(f"Last {a} ({year})",
                max([str(r["activity_date"])[:10] for r in acts if r["activity"] == a and r.get("year") == year] or ["-"]))
               for a in FARM_ACTIVITIES], h=18)
    if acts:
        ev = [(r["activity"], pd.to_datetime(str(r["activity_date"])[:10]), r.get("field") or "") for r in acts]
        pdf.chart(chart_events(ev, FARM_ACTIVITIES, f"Field Work Timeline"))
        pdf.section("Field Work Log", level=1)
        pdf.table(["Date", "Activity", "Field / area", "Notes"],
                  [[str(r["activity_date"])[:10], r["activity"], r.get("field"), r.get("notes")]
                   for r in sorted(acts, key=lambda r: str(r["activity_date"]), reverse=True)],
                  widths=(22, 38, 36, 96))
    else:
        pdf.note("No field work logged yet.")
    yp = plans.get(year) or {}
    if any(clean(v) for v in yp.values()):
        pdf.section(f"Plot Notes - {year}", level=1)
        for loc in FARM_PLAN_LOCATIONS:
            txt = clean(yp.get(loc))
            if not txt:
                continue
            pdf.ensure(14)
            pdf.set_font("Helvetica", "B", 9.5)
            pdf.set_text_color(*_rgb(GREEN))
            pdf.cell(0, 5.5, loc, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.para(txt, size=8.8)
            pdf.ln(1.5)
    pm = None
    for ext in (".jpeg", ".jpg", ".png", ".webp"):
        if (FARM_MAPS_DIR / f"{year}-planting-map{ext}").exists():
            pm = FARM_MAPS_DIR / f"{year}-planting-map{ext}"
            break
    ov = FARM_MAPS_DIR / "overview-labeled-key.png"
    if ov.exists() or pm:
        pdf.add_page()
        pdf.section("Farm Maps", level=1)
        pdf.photo(ov, "Master schematic (same every year)", max_h=96)
        if pm:
            pdf.photo(pm, f"{year} food plot planting map", max_h=96)


def sec_pumping(pdf: DDReport, runs: list, years: list, new_page=True):
    pdf.section("Pumping - Farm Wells", "Main and South Block well runs. Runtime = turned off minus turned on; a missing time counts as midnight.",
                new_page=new_page)
    hrs = {w: sum(_run_hours(r) or 0 for r in runs if r.get("well") == w) for w in WELLS}
    days = {w: sum(_run_days(r) or 0 for r in runs if r.get("well") == w) for w in WELLS}
    running = [r for r in runs if r.get("on_date") and not r.get("off_date")]
    pdf.cards([("Total runtime", f"{sum(hrs.values()):,.1f} hrs", f"{len(runs)} runs"),
               ("Main well", f"{hrs['Main']:,.1f} hrs", f"{days['Main']} days"),
               ("South Block well", f"{hrs['South Block']:,.1f} hrs", f"{days['South Block']} days"),
               ("Running now", len(running), ", ".join(r["well"] for r in running) or "all off")])
    tl = []
    for r in sorted(runs, key=lambda r: str(r.get("on_date"))):
        b = _run_bounds(r)
        if b:
            col = GREEN if r.get("well") == "Main" else BROWN
            tl.append((f"{r.get('well')} ({b[0].year})", b[0], b[1], col,
                       f"{_run_days(r)} days / {_run_hours(r):,.0f} hrs"))
    if tl:
        pdf.chart(chart_timeline(tl, "Well Run Timeline"))
    yr_days = {w: [] for w in WELLS}
    for y in years:
        for w in WELLS:
            yr_days[w].append(sum(_run_days(r) or 0 for r in runs if r.get("well") == w and _run_year(r) == y))
    pdf.chart_pair(chart_bar(WELLS, [round(hrs[w], 1) for w in WELLS], "Runtime Hours by Well", fmt="{:,.1f}"),
                   chart_grouped([str(y) for y in years], {w: yr_days[w] for w in WELLS}, "Pumping Days by Year",
                                 w=3.6, fmt="{:g}"))
    pdf.callout("Strategy", PUMPING_STRATEGY)
    pdf.section("Well Runs", level=1)
    pdf.table(["Year", "Well", "Turned on", "Turned off", "Days run", "Runtime (hrs)", "Notes"],
              [[_run_year(r), r.get("well"), f"{r.get('on_date') or ''} {str(r.get('on_time') or '')[:5]}".strip(),
                f"{r.get('off_date') or ''} {str(r.get('off_time') or '')[:5]}".strip(),
                (f"{_run_days(r)} days" if r.get("off_date") else f"running ({_run_days(r)} so far)"),
                round(_run_hours(r) or 0, 1), r.get("notes")] for r in sorted(runs, key=lambda r: str(r.get("on_date")), reverse=True)],
              widths=(12, 22, 26, 26, 18, 18, 70), align=("CENTER", "LEFT", "LEFT", "LEFT", "CENTER", "CENTER", "LEFT"), size=7.4)
    pdf.section("Pumping by Year", level=1)
    rows = []
    for y in years:
        row = [y]
        for w in WELLS:
            wr = [r for r in runs if r.get("well") == w and _run_year(r) == y]
            ons = sorted(str(r["on_date"])[:10] for r in wr if r.get("on_date"))
            row += [sum(_run_days(r) or 0 for r in wr), round(sum(_run_hours(r) or 0 for r in wr), 1), ons[0] if ons else ""]
        rows.append(row)
    pdf.table(["Year", "Main days", "Main hrs", "Main first on", "SB days", "SB hrs", "SB first on"], rows,
              align=["CENTER"] * 7)


def sec_rainfall(pdf: DDReport, rain: dict, new_page=False):
    pdf.section("Pumping - Rainfall", "Jul-Oct farm rainfall (inches). Auto-filled from Open-Meteo for the farm; gauge readings override.",
                new_page=new_page)
    years = sorted(rain)
    tots = {y: round(sum(rain[y]["vals"].values()), 2) for y in years}
    pdf.cards([(f"Jul-Oct {y}", f'{tots[y]:.2f}"', "gauge + auto" if "gauge" in rain[y]["src"].values() else "auto (Open-Meteo)")
               for y in years[-4:]])
    months = list(RAIN_MONTHS.values())
    pdf.chart(chart_grouped(months, {y: [rain[y]["vals"].get(m) for m in RAIN_MONTHS] for y in years},
                            "Monthly Rainfall by Year (in)", ylabel="inches"))
    rows = []
    for y in sorted(years, reverse=True):
        v, s = rain[y]["vals"], rain[y]["src"]
        rows.append([y] + [round(v[m], 2) if m in v else "" for m in RAIN_MONTHS] + [tots[y],
                    ", ".join(RAIN_MONTHS[m] for m in s if s[m] == "gauge")])
    pdf.table(["Year"] + months + ["Total", "Gauge overrides"], rows, align=["CENTER"] * 6 + ["LEFT"])


def sec_maintenance(pdf: DDReport, assets: list, log: list, runs: list, new_page=True):
    pdf.section("Maintenance", "Service log, last service per asset, costs, and cost of ownership.", new_page=new_page)
    by_id = {a["id"]: a for a in assets}
    log = sorted(log, key=lambda r: str(r["service_date"]), reverse=True)
    today = datetime.now(CLUB_TZ).date()
    pu = next((a for a in assets if a["name"].strip().lower() == "power unit"), None)
    cards = [("Assets", len(assets), f"{sum(1 for a in assets if a.get('active'))} active"),
             ("Service entries", len(log)),
             ("Maintenance spend", f"${sum(_num(r.get('cost')) or 0 for r in log):,.0f}")]
    if pu:
        pul = [r for r in log if r["asset_id"] == pu["id"]]
        if pul:
            since = datetime.combine(date.fromisoformat(str(pul[0]["service_date"])[:10]), datetime.min.time())
            h = sum(x for x in (_run_hours(r, since=since) for r in runs if _run_bounds(r) and _run_bounds(r)[1] > since) if x)
            cards.append(("Power Unit hrs since service", f"{h:,.1f} hrs", f"since {str(pul[0]['service_date'])[:10]} ({pul[0]['service_type']})"))
    pdf.cards(cards)
    if log:
        cost_asset = {}
        cost_year = {}
        for r in log:
            nm = by_id.get(r["asset_id"], {"name": "?"})["name"]
            c = _num(r.get("cost")) or 0
            cost_asset[nm] = cost_asset.get(nm, 0) + c
            y = str(r["service_date"])[:4]
            cost_year[y] = cost_year.get(y, 0) + c
        ca = sorted(cost_asset.items(), key=lambda kv: -kv[1])
        pdf.chart_pair(chart_barh([k for k, _ in ca], [v for _, v in ca], "Maintenance Cost by Asset ($)", fmt="${:,.0f}"),
                       chart_bar(sorted(cost_year), [cost_year[y] for y in sorted(cost_year)], "Costs by Year ($)",
                                 color=BROWN, fmt="${:,.0f}"))
    pdf.section("Last Service", level=1)
    rows = []
    for a in assets:
        ar = [r for r in log if r["asset_id"] == a["id"]]
        last = ar[0] if ar else None
        mr = _num(last.get("meter_reading")) if last else None
        rows.append([a["name"], "Yes" if a.get("active") else "No", str(last["service_date"])[:10] if last else "-",
                     last["service_type"] if last else "", f"{mr:g} {a.get('meter_unit') or ''}".strip() if mr is not None else "",
                     (today - date.fromisoformat(str(last["service_date"])[:10])).days if last else ""])
    pdf.table(["Asset", "Active", "Last service", "Type", "Meter", "Days since"], rows,
              align=("LEFT", "CENTER", "CENTER", "LEFT", "CENTER", "CENTER"))
    pdf.section("Service Log", level=1)
    pdf.table(["Date", "Asset", "Type", "Meter", "Cost $", "Done by", "Notes"],
              [[str(r["service_date"])[:10], by_id.get(r["asset_id"], {"name": "?"})["name"], r["service_type"],
                _num(r.get("meter_reading")), _num(r.get("cost")), r.get("done_by"), r.get("notes")] for r in log],
              widths=(20, 24, 24, 14, 14, 18, 78))
    if log:
        pdf.section("Costs by Year", level=1)
        yrs = sorted({str(r["service_date"])[:4] for r in log})
        rows = []
        for a in assets:
            vals = [sum(_num(r.get("cost")) or 0 for r in log if r["asset_id"] == a["id"] and str(r["service_date"])[:4] == y) for y in yrs]
            if sum(vals):
                rows.append([a["name"]] + [round(v, 2) for v in vals] + [round(sum(vals), 2)])
        pdf.table(["Asset"] + yrs + ["Total"], rows, align=["LEFT"] + ["CENTER"] * (len(yrs) + 1))
    pdf.section("Cost of Ownership", level=1)
    rows = []
    for a in assets:
        m = sum(_num(r.get("cost")) or 0 for r in log if r["asset_id"] == a["id"])
        p = _num(a.get("purchase_price"))
        rows.append([a["name"], str(a.get("purchase_date") or "")[:10] or "-", p if p is not None else "-", round(m, 2), round((p or 0) + m, 2)])
    pdf.table(["Asset", "Purchased", "Purchase $", "Maintenance $", "Total cost $"], rows,
              align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER"))
    pdf.section("Assets", level=1)
    pdf.table(["Asset", "Model year", "Meter", "Purchased", "Purchase $", "Active", "Notes"],
              [[a["name"], a.get("model_year") or "", a.get("meter_unit") or "", str(a.get("purchase_date") or "")[:10],
                _num(a.get("purchase_price")), "Yes" if a.get("active") else "No", a.get("notes")] for a in assets],
              align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER", "CENTER", "LEFT"))


def ebird_rows(df: pd.DataFrame) -> list[list]:
    out = []
    for _, r in df.sort_values("date").iterrows():
        for sp in SPECIES:
            c = int(r.get(SPECIES_COLS[sp], 0) or 0)
            if c > 0:
                out.append([sp, c, pd.to_datetime(r["date"]).strftime("%Y-%m-%d"), r.get("location") or "", "Stationary",
                            "From DD Lodge hunt log"])
    return out


def ebird_csv(df: pd.DataFrame) -> str:
    lines = ["Common Name,Count,Date,Location,Protocol,Notes\n"]
    for sp, c, d, loc, proto, note in ebird_rows(df):
        lines.append(f'"{sp}",{c},{d},"{loc}","{proto}","{note}"\n')
    return "".join(lines)


def _period(df: pd.DataFrame, season: str | None):
    if season and season != "All" and not df.empty:
        return df[df["season"] == season], season
    return df, "All Seasons"


# ---------------- public builders ----------------
def build_club_report(hunts: pd.DataFrame, season: str | None = None) -> bytes:
    """Rebuilt 'Generate PDF Club Report' (Reports & Exports)."""
    df, period = _period(hunts, season)
    pdf = DDReport("DD Hunt Report", f"Club report - {period}")
    pdf.add_page()
    if df.empty:
        pdf.para("No hunts in this period.")
        return bytes(pdf.output())
    sec_dashboard(pdf, df, period)
    sec_species_table(pdf, df)
    pdf.add_page()
    sec_analytics(pdf, df)
    sec_breakdowns(pdf, df)
    sec_hunt_log(pdf, df)
    return bytes(pdf.output())


def build_history_report(hunts: pd.DataFrame, season: str | None = None) -> bytes:
    """Rebuilt Hunt History export (was CSV only): pivot charts + full log."""
    df, period = _period(hunts, season)
    pdf = DDReport("Hunt History Report", f"Hunt history and pivots - {period}")
    pdf.add_page()
    if df.empty:
        pdf.para("No hunts in this period.")
        return bytes(pdf.output())
    st = hunt_stats(df)
    pdf.section("Quick Period Stats")
    pdf.cards([("Ducks in Selected Period", st["total"]), ("Hunts", st["hunts"]),
               ("Hunter Avg", f"{st['bph']:.2f}" if st["bph"] is not None else "-")], h=18)
    loc = group_pivot(df, "location", "Location")
    sea = group_pivot(df, "season", "Season")
    pdf.chart_pair(chart_bar(list(loc["Location"]), [int(v) for v in loc["Ducks"]], "Ducks by Location"),
                   chart_bar(list(sea["Season"]), [int(v) for v in sea["Ducks"]], "Ducks by Season", color=BROWN))
    for key, label in (("location", "Location"), ("season", "Season")):
        g = group_pivot(df, key, label)
        pdf.section(f"Pivot by {label}", level=1)
        pdf.table([label, "Hunts", "Ducks", "Hunter Slots", "Birds / Hunter"],
                  g[[label, "Hunts", "Ducks", "Hunter Slots", "Birds / Hunter"]].values.tolist(),
                  align=("LEFT", "CENTER", "CENTER", "CENTER", "CENTER"))
    sec_species_table(pdf, df)
    hp = hunter_pivot(df)
    pdf.section("Pivot by Hunter", "Club-style attribution: each hunter is credited the full day's bag. Names as entered.",
                new_page=True)
    pdf.chart(chart_barh(list(hp["Hunter"]), [int(v) for v in hp["Ducks"]], "Ducks on Hunts Attended, by Hunter",
                         w=7.2, h=min(3.4, 0.22 * len(hp) + 0.9), color=GREEN))
    pdf.table(["Hunter", "Hunts", "Ducks", "Birds / Hunter-Day"], hp.values.tolist(),
              align=("LEFT", "CENTER", "CENTER", "CENTER"))
    sec_hunt_log(pdf, df)
    return bytes(pdf.output())


def build_ebird_report(hunts: pd.DataFrame, season: str | None = None) -> bytes:
    """PDF companion to the eBird checklist CSV (the CSV itself stays for eBird import)."""
    df, period = _period(hunts, season)
    pdf = DDReport("eBird-style Checklist", f"Species checklist - {period}")
    pdf.add_page()
    rows = ebird_rows(df)
    if not rows:
        pdf.para("No species counts in this period.")
        return bytes(pdf.output())
    tot = {k: v for k, v in species_totals(df).items() if v}
    pdf.section("Checklist Summary", "Species totals from the hunt log, formatted like the eBird CSV export.")
    pdf.cards([("Species", len(tot)), ("Individuals", sum(tot.values())), ("Checklists (hunt days)", len(df)),
               ("Locations", df["location"].nunique())])
    items = sorted(tot.items(), key=lambda kv: -kv[1])
    pdf.chart_pair(chart_pie(tot, "Share of Individuals"),
                   chart_barh([k for k, _ in items], [v for _, v in items], "Count by Species", color=BROWN))
    pdf.section("Checklist Rows", level=1)
    pdf.table(["Common Name", "Count", "Date", "Location", "Protocol", "Notes"], rows,
              widths=(32, 16, 24, 30, 26, 64), align=("LEFT", "CENTER", "CENTER", "LEFT", "LEFT", "LEFT"))
    pdf.note("Upload the CSV version to eBird; adjust Protocol or add exact coordinates before uploading.")
    return bytes(pdf.output())


def build_master_report(data: dict, season: str | None = None) -> tuple[bytes, list[str]]:
    """Everything in the app, one PDF. Returns (pdf_bytes, skipped_section_notes)."""
    hunts, period = _period(data.get("hunts", pd.DataFrame()), season)
    sdf = data.get("surveys", pd.DataFrame())
    acts = data.get("farm_activities") or []
    plans = data.get("farm_plans") or {}
    runs = data.get("well_runs") or []
    rain = {y: v for y, v in (data.get("rainfall") or {}).items() if v.get("vals")}
    assets = data.get("assets") or []
    mlog = data.get("maintenance_log") or []
    year = data.get("farm_year") or datetime.now(CLUB_TZ).year
    skipped = []

    pdf = DDReport("Master Report", f"Everything in DD Hunt Tracker - hunts: {period}")
    # ---- cover ----
    pdf.add_page()
    if LOGO_PATH.exists():
        pdf.image(str(LOGO_PATH), x=(pdf.w - 60) / 2, y=40, w=60)
    pdf.set_y(106)
    pdf.set_font("Helvetica", "B", 26)
    pdf.set_text_color(*_rgb(GREEN))
    pdf.cell(0, 12, "DD Hunt Tracker", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "B", 18)
    pdf.set_text_color(*_rgb(BROWN))
    pdf.cell(0, 10, "Master Report", align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.set_font("Helvetica", "", 11)
    pdf.set_text_color(*_rgb(SLATE))
    pdf.cell(0, 7, clean(f"Hunts: {period}   |   Farm & pumping: all years   |   Generated {pdf.generated}"),
             align="C", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(8)
    if not hunts.empty:
        st = hunt_stats(hunts)
        pdf.cards([("Total Ducks", st["total"]), ("Hunting Days", st["hunts"]), ("Hunter Avg", f"{st['bph']:.2f}" if st["bph"] else "-"),
                   ("Wildlife Surveys", len(sdf)), ("Well Runtime", f"{sum(_run_hours(r) or 0 for r in runs):,.0f} hrs")])
    pdf.add_page()
    pdf.insert_toc_placeholder(_toc, pages=1)

    # ---- hunting ----
    if hunts.empty:
        skipped.append("Dashboard / Season Analytics / Hunt Log / Pivots - no hunts in this period")
    else:
        sec_dashboard(pdf, hunts, period)  # ToC placeholder already opened a fresh page
        sec_analytics(pdf, hunts)
        pdf.add_page()
        sec_breakdowns(pdf, hunts)
        sec_pivots(pdf, hunts)
        sec_species_table(pdf, hunts)
        sec_hunt_log(pdf, hunts)
    if sdf is None or sdf.empty:
        skipped.append("Wildlife Survey - no surveys logged")
    else:
        sec_wildlife(pdf, sdf)
    if not acts and not any(clean(v) for v in (plans.get(year) or {}).values()):
        skipped.append("Farm Plans - no field work or plot notes")
    else:
        sec_farm(pdf, acts, plans, year)
    if not runs:
        skipped.append("Pumping - Farm Wells - no well runs logged")
    else:
        yrs = sorted({_run_year(r) for r in runs if _run_year(r)} | set(range(year - 2, year + 1)), reverse=True)
        sec_pumping(pdf, runs, sorted(yrs))
    if not rain:
        skipped.append("Pumping - Rainfall - no gauge readings and Open-Meteo unavailable")
    else:
        sec_rainfall(pdf, rain, new_page=True)
    if not assets:
        skipped.append("Maintenance - no assets")
    else:
        sec_maintenance(pdf, assets, mlog, runs)

    # ---- about this report ----
    pdf.section("About This Report", new_page=False)
    pdf.para("Built from the live DD Hunt Tracker data the moment it was generated. Charts match the app's pages; "
             "tables list every entry. Hunter names appear exactly as entered on each hunt report.")
    if skipped:
        pdf.para("Sections skipped (no data): " + "; ".join(skipped))
    else:
        pdf.para("Sections skipped: none - every section had data.")
    return bytes(pdf.output()), skipped


def build_excel_workbook(hunts: pd.DataFrame, season: str | None = None) -> bytes:
    """Hunt history export as .xlsx with native Excel charts (opens in Excel / Numbers / Google Sheets)."""
    from openpyxl import Workbook
    from openpyxl.chart import BarChart, LineChart, PieChart, Reference
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    df, period = _period(hunts, season)
    wb = Workbook()
    head_fill = PatternFill("solid", fgColor=GREEN.lstrip("#"))
    head_font = Font(bold=True, color=CREAM.lstrip("#"))

    def sheet(ws, headers, rows, widths=None):
        ws.append(headers)
        for c in ws[1]:
            c.fill, c.font, c.alignment = head_fill, head_font, Alignment(horizontal="center", wrap_text=True)
        for r in rows:
            ws.append([None if (isinstance(v, float) and pd.isna(v)) else v for v in r])
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(h, max(10, len(str(h)) + 2))
        ws.freeze_panes = "A2"

    d = df.sort_values("date")
    ws = wb.active
    ws.title = "Hunt Log"
    sheet(ws, ["Date", "Season", "Location", "Hunters", "Hunter Count", "Ducks", "Birds / Hunter", "Wind", "High F",
               "Low F", "River", "Notes"] + SPECIES,
          [[pd.to_datetime(r["date"]).date(), r.get("season"), r.get("location"), ", ".join(parse_hunters(r.get("hunters"))),
            len(parse_hunters(r.get("hunters"))), int(r["daily_total"]),
            round(r["daily_total"] / max(len(parse_hunters(r.get("hunters"))), 1), 2), r.get("wind"), r.get("temp_high"),
            r.get("temp_low"), r.get("river_level"), r.get("notes")] + [int(r[SPECIES_COLS[sp]]) for sp in SPECIES]
           for _, r in d.iterrows()], {"Hunters": 60, "Notes": 30, "Date": 12})
    if len(d):
        ch = LineChart(); ch.title = "Ducks per Hunt Day"; ch.y_axis.title = "Ducks"; ch.height, ch.width = 7, 16
        ch.add_data(Reference(ws, min_col=6, min_row=1, max_row=len(d) + 1), titles_from_data=True)
        ch.set_categories(Reference(ws, min_col=1, min_row=2, max_row=len(d) + 1))
        ws.add_chart(ch, f"A{len(d) + 4}")

    tot = sorted(((k, v) for k, v in species_totals(df).items() if v), key=lambda kv: -kv[1])
    ws2 = wb.create_sheet("Species")
    sheet(ws2, ["Species", "Birds"], tot, {"Species": 16})
    if tot:
        pie = PieChart(); pie.title = "Harvest by Species"; pie.height, pie.width = 8, 12
        pie.add_data(Reference(ws2, min_col=2, min_row=1, max_row=len(tot) + 1), titles_from_data=True)
        pie.set_categories(Reference(ws2, min_col=1, min_row=2, max_row=len(tot) + 1))
        ws2.add_chart(pie, "D2")

    hp = hunter_pivot(df)
    ws3 = wb.create_sheet("By Hunter")
    sheet(ws3, ["Hunter", "Hunts", "Ducks", "Birds / Hunter-Day"], hp.values.tolist(), {"Hunter": 18})
    loc = group_pivot(df, "location", "Location")
    ws4 = wb.create_sheet("By Location")
    sheet(ws4, ["Location", "Hunts", "Ducks", "Hunter Slots", "Birds / Hunter"],
          loc[["Location", "Hunts", "Ducks", "Hunter Slots", "Birds / Hunter"]].values.tolist(), {"Location": 16})
    for wsx, n, title in ((ws3, len(hp), "Ducks on Hunts Attended"), (ws4, len(loc), "Ducks by Location")):
        if n:
            b = BarChart(); b.type = "bar" if wsx is ws3 else "col"; b.title = title; b.height, b.width = 8, 14
            b.add_data(Reference(wsx, min_col=3, min_row=1, max_row=n + 1), titles_from_data=True)
            b.set_categories(Reference(wsx, min_col=1, min_row=2, max_row=n + 1))
            b.legend = None
            wsx.add_chart(b, "G2")
    wb.properties.title = f"DD Hunt History - {period}"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
