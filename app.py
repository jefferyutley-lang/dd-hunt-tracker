#!/usr/bin/env python3
"""
DD Hunt Tracker
Enhanced Streamlit app for duck club daily hunting logs.
Features: photo attachments, season tracking, multi-user roles, PDF reports, eBird export, PWA-ready UI.
Logo: DD Lodge Entrance Logo
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import sqlite3
from datetime import date, datetime
from pathlib import Path
import os
from fpdf import FPDF
import requests

# ---------------- CONFIG ----------------
st.set_page_config(
    page_title="DD Hunt Tracker",
    page_icon="🦆",
    layout="wide",
    initial_sidebar_state="expanded"
)

BASE_DIR = Path(__file__).parent
DB_PATH = BASE_DIR / "duck_hunt.db"
UPLOAD_DIR = BASE_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)
LOGO_PATH = BASE_DIR / "logo-1.png"

# Species exactly matching the paper form
SPECIES = [
    "Mallard", "Gadwall", "Teal", "Pintail", "Wood Duck",
    "Widgeon", "Shoveler", "Canvasback", "Redhead", "Divers", "Geese"
]
SPECIES_COLS = {sp: sp.lower().replace(" ", "_") + "_count" for sp in SPECIES}

CLUB_NAME = "DD"
APP_TITLE = f"🦆 {CLUB_NAME} Hunt Tracker"


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_season_from_date(d: date) -> str:
    """Duck season: Sept–Feb typical. Returns 'YYYY-YYYY' string."""
    year = d.year
    if d.month >= 9:
        return f"{year}-{year + 1}"
    else:
        return f"{year - 1}-{year}"


def init_db():
    """Create tables + migrate schema safely."""
    conn = get_db_connection()
    c = conn.cursor()

    # Core hunts table
    c.execute("""
        CREATE TABLE IF NOT EXISTS hunts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            location TEXT,
            wind TEXT,
            temp_high INTEGER,
            temp_low INTEGER,
            river_level TEXT,
            mallard_count INTEGER DEFAULT 0,
            gadwall_count INTEGER DEFAULT 0,
            teal_count INTEGER DEFAULT 0,
            pintail_count INTEGER DEFAULT 0,
            wood_duck_count INTEGER DEFAULT 0,
            widgeon_count INTEGER DEFAULT 0,
            shoveler_count INTEGER DEFAULT 0,
            canvasback_count INTEGER DEFAULT 0,
            redhead_count INTEGER DEFAULT 0,
            divers_count INTEGER DEFAULT 0,
            geese_count INTEGER DEFAULT 0,
            notes TEXT,
            season TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Hunters junction
    c.execute("""
        CREATE TABLE IF NOT EXISTS hunt_hunters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hunt_id INTEGER NOT NULL,
            hunter_name TEXT NOT NULL,
            FOREIGN KEY (hunt_id) REFERENCES hunts(id) ON DELETE CASCADE
        )
    """)

    # Photos table (new)
    c.execute("""
        CREATE TABLE IF NOT EXISTS hunt_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hunt_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            caption TEXT,
            uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (hunt_id) REFERENCES hunts(id) ON DELETE CASCADE
        )
    """)

    # Add season column if missing (migration)
    try:
        c.execute("ALTER TABLE hunts ADD COLUMN season TEXT")
    except sqlite3.OperationalError:
        pass

    # Add rainfall column if missing (migration)
    try:
        c.execute("ALTER TABLE hunts ADD COLUMN rainfall REAL DEFAULT 0")
    except sqlite3.OperationalError:
        pass

    conn.commit()

    # Backfill season for any existing rows
    c.execute("SELECT id, date FROM hunts WHERE season IS NULL OR season = ''")
    for row in c.fetchall():
        try:
            d = datetime.strptime(row[1], "%Y-%m-%d").date()
            season = get_season_from_date(d)
            c.execute("UPDATE hunts SET season = ? WHERE id = ?", (season, row[0]))
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_all_hunts_df(season_filter: str | None = None) -> pd.DataFrame:
    """Return hunts with daily_total, hunters, and optional season filter."""
    conn = get_db_connection()
    query = """
        SELECT 
            h.*,
            GROUP_CONCAT(hh.hunter_name, ' | ') AS hunters
        FROM hunts h
        LEFT JOIN hunt_hunters hh ON h.id = hh.hunt_id
        GROUP BY h.id
        ORDER BY h.date DESC, h.id DESC
    """
    df = pd.read_sql_query(query, conn)
    conn.close()

    if df.empty:
        return df

    count_cols = [col for col in df.columns if col.endswith("_count")]
    df["daily_total"] = df[count_cols].sum(axis=1).astype(int)

    if "season" not in df.columns or df["season"].isna().all():
        df["season"] = df["date"].apply(
            lambda x: get_season_from_date(datetime.strptime(x, "%Y-%m-%d").date())
        )

    if season_filter and season_filter != "All":
        df = df[df["season"] == season_filter]

    return df


def add_hunt(data: dict, hunters: list[str]) -> int:
    """Insert hunt + hunters. Auto-computes season if missing."""
    conn = get_db_connection()
    c = conn.cursor()

    if "season" not in data or not data.get("season"):
        d = datetime.fromisoformat(data["date"]).date()
        data["season"] = get_season_from_date(d)

    count_cols = list(SPECIES_COLS.values())
    all_cols = ["date", "location", "wind", "temp_high", "temp_low", "river_level", "rainfall", "notes", "season"] + count_cols
    placeholders = ", ".join(["?"] * len(all_cols))
    col_names = ", ".join(all_cols)

    values = [
        data.get("date"), data.get("location"), data.get("wind"),
        data.get("temp_high"), data.get("temp_low"), data.get("river_level"),
        data.get("rainfall", 0.0),
        data.get("notes"), data.get("season")
    ]
    for sp in SPECIES:
        values.append(int(data.get(SPECIES_COLS[sp], 0)))

    c.execute(f"INSERT INTO hunts ({col_names}) VALUES ({placeholders})", values)
    hunt_id = c.lastrowid

    for name in hunters:
        name = name.strip()
        if name:
            c.execute("INSERT INTO hunt_hunters (hunt_id, hunter_name) VALUES (?, ?)", (hunt_id, name))

    conn.commit()
    conn.close()
    return hunt_id


def get_hunt_details(hunt_id: int) -> dict:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM hunts WHERE id = ?", (hunt_id,))
    hunt_row = c.fetchone()
    if not hunt_row:
        conn.close()
        return {}
    hunt = dict(hunt_row)

    c.execute("SELECT hunter_name FROM hunt_hunters WHERE hunt_id = ? ORDER BY id", (hunt_id,))
    hunt["hunters"] = [r[0] for r in c.fetchall()]

    conn.close()
    return hunt


def update_hunt(hunt_id: int, data: dict, hunters: list[str]):
    conn = get_db_connection()
    c = conn.cursor()

    if "season" not in data or not data.get("season"):
        d = datetime.fromisoformat(data["date"]).date()
        data["season"] = get_season_from_date(d)

    count_cols = list(SPECIES_COLS.values())
    set_cols = ["date", "location", "wind", "temp_high", "temp_low", "river_level", "notes", "season"] + count_cols
    set_clause = ", ".join([f"{col} = ?" for col in set_cols])

    values = [
        data.get("date"), data.get("location"), data.get("wind"),
        data.get("temp_high"), data.get("temp_low"), data.get("river_level"),
        data.get("rainfall", 0.0),
        data.get("notes"), data.get("season")
    ]
    for sp in SPECIES:
        values.append(int(data.get(SPECIES_COLS[sp], 0)))
    values.append(hunt_id)

    c.execute(f"UPDATE hunts SET {set_clause} WHERE id = ?", values)

    c.execute("DELETE FROM hunt_hunters WHERE hunt_id = ?", (hunt_id,))
    for name in hunters:
        name = name.strip()
        if name:
            c.execute("INSERT INTO hunt_hunters (hunt_id, hunter_name) VALUES (?, ?)", (hunt_id, name))

    conn.commit()
    conn.close()


def delete_hunt(hunt_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    # Photos and files cleaned by cascade + manual
    c.execute("SELECT filename FROM hunt_photos WHERE hunt_id = ?", (hunt_id,))
    for row in c.fetchall():
        try:
            (UPLOAD_DIR / row[0]).unlink(missing_ok=True)
        except:
            pass
    c.execute("DELETE FROM hunt_photos WHERE hunt_id = ?", (hunt_id,))
    c.execute("DELETE FROM hunt_hunters WHERE hunt_id = ?", (hunt_id,))
    c.execute("DELETE FROM hunts WHERE id = ?", (hunt_id,))
    conn.commit()
    conn.close()


def add_photos_to_hunt(hunt_id: int, uploaded_files: list, captions: list[str] | None = None):
    """Save uploaded images and link to hunt."""
    if captions is None:
        captions = [""] * len(uploaded_files)
    for i, up_file in enumerate(uploaded_files):
        if up_file is None:
            continue
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in up_file.name if c.isalnum() or c in "._-").rstrip() or "photo.jpg"
        filename = f"hunt{hunt_id}_{timestamp}_{safe_name}"
        file_path = UPLOAD_DIR / filename
        with open(file_path, "wb") as f:
            f.write(up_file.getbuffer())

        cap = captions[i].strip() if i < len(captions) else ""
        conn = get_db_connection()
        c = conn.cursor()
        c.execute(
            "INSERT INTO hunt_photos (hunt_id, filename, caption) VALUES (?, ?, ?)",
            (hunt_id, filename, cap)
        )
        conn.commit()
        conn.close()


def get_hunt_photos(hunt_id: int) -> list[dict]:
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("""
        SELECT id, filename, caption, uploaded_at 
        FROM hunt_photos 
        WHERE hunt_id = ? 
        ORDER BY uploaded_at DESC
    """, (hunt_id,))
    photos = []
    for row in c.fetchall():
        photos.append({
            "id": row[0],
            "filename": row[1],
            "caption": row[2] or "",
            "uploaded_at": row[3],
            "full_path": str(UPLOAD_DIR / row[1])
        })
    conn.close()
    return photos


def delete_photo(photo_id: int):
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT filename FROM hunt_photos WHERE id = ?", (photo_id,))
    row = c.fetchone()
    if row:
        try:
            (UPLOAD_DIR / row[0]).unlink(missing_ok=True)
        except:
            pass
        c.execute("DELETE FROM hunt_photos WHERE id = ?", (photo_id,))
    conn.commit()
    conn.close()


def load_sample_data() -> bool:
    """Load demo hunts if DB empty."""
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM hunts")
    if c.fetchone()[0] > 0:
        conn.close()
        return False

    samples = [
        {
            "date": "2025-11-15", "location": "North Levee - Club Lease",
            "wind": "NW 10-15 mph, gusty", "temp_high": 54, "temp_low": 39,
            "river_level": "Normal 7.2 ft - falling",
            "notes": "Excellent morning flight. Big mallards and gadwalls. Wood ducks late.",
            "counts": {"Mallard": 11, "Gadwall": 7, "Teal": 5, "Pintail": 3, "Wood Duck": 2,
                       "Widgeon": 1, "Shoveler": 4, "Canvasback": 0, "Redhead": 0, "Divers": 0, "Geese": 0},
            "hunters": ["Jeff Utley", "Mike Thompson", "Chris Reed"]
        },
        {
            "date": "2025-11-22", "location": "South Blind - Backwater",
            "wind": "SE 5-8 mph, light", "temp_high": 48, "temp_low": 32,
            "river_level": "Low 6.4 ft",
            "notes": "Slow start then teal and widgeon picked up. Divers in distance.",
            "counts": {"Mallard": 4, "Gadwall": 3, "Teal": 9, "Pintail": 1, "Wood Duck": 0,
                       "Widgeon": 4, "Shoveler": 2, "Canvasback": 0, "Redhead": 1, "Divers": 2, "Geese": 0},
            "hunters": ["Jeff Utley", "David Kline", "Sarah Patel"]
        },
        {
            "date": "2025-12-06", "location": "Club Main Pond",
            "wind": "N 15-20 mph, cold", "temp_high": 38, "temp_low": 22,
            "river_level": "Rising 8.1 ft - muddy",
            "notes": "Tough but limited on mallards. Canvasbacks and redheads with the wind. Memorable!",
            "counts": {"Mallard": 14, "Gadwall": 2, "Teal": 1, "Pintail": 0, "Wood Duck": 0,
                       "Widgeon": 0, "Shoveler": 0, "Canvasback": 3, "Redhead": 4, "Divers": 1, "Geese": 2},
            "hunters": ["Jeff Utley", "Mike Thompson", "Robert Hayes", "Tom Wilson"]
        },
        {
            "date": "2026-01-10", "location": "North Levee - Club Lease",
            "wind": "W 12 mph", "temp_high": 45, "temp_low": 28,
            "river_level": "Normal 7.0 ft",
            "notes": "Late season divers and redheads. Geese high. Nice mallard pairs to close strong.",
            "counts": {"Mallard": 6, "Gadwall": 5, "Teal": 0, "Pintail": 2, "Wood Duck": 1,
                       "Widgeon": 2, "Shoveler": 1, "Canvasback": 2, "Redhead": 5, "Divers": 7, "Geese": 3},
            "hunters": ["Jeff Utley", "Chris Reed"]
        }
    ]

    for s in samples:
        data = {k: s[k] for k in ["date", "location", "wind", "temp_high", "temp_low", "river_level", "notes"]}
        for sp in SPECIES:
            data[SPECIES_COLS[sp]] = s["counts"].get(sp, 0)
        add_hunt(data, s["hunters"])

    conn.close()
    return True


# ---------------- AUTO-FILL HELPERS (River + Weather) ----------------
def get_river_level_usgs(target_date: date) -> str | None:
    """Fetch daily gage height (ft) for New Madrid USGS site 07024175 (Mississippi River)."""
    try:
        start = target_date.isoformat()
        end = target_date.isoformat()
        url = (
            "https://waterservices.usgs.gov/nwis/dv/"
            f"?format=json&sites=07024175&parameterCd=00065&startDT={start}&endDT={end}"
        )
        resp = requests.get(url, timeout=12)
        if resp.status_code != 200:
            return None
        data = resp.json()
        ts = data.get("value", {}).get("timeSeries", [])
        if not ts:
            return None
        values = ts[0].get("values", [{}])[0].get("value", [])
        if not values:
            return None
        val = float(values[0]["value"])
        return f"{val:.1f} ft"
    except Exception:
        return None


def get_weather_open_meteo(target_date: date, lat: float = 36.68218, lon: float = -89.37869) -> dict | None:
    """
    Smart weather fetch:
    - If date == today: Use forecast API to get rainfall so far today + daily values
    - If date is in the past: Use historical archive API for accurate full-day rainfall
    """
    today = date.today()
    try:
        if target_date == today:
            # Today → use forecast for "rainfall so far today"
            url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                "&current=precipitation"
                "&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max,winddirection_10m_dominant"
                "&timezone=America/Chicago&temperature_unit=fahrenheit&wind_speed_unit=mph"
            )
            resp = requests.get(url, timeout=12)
            if resp.status_code != 200:
                return None
            data = resp.json()

            daily = data.get("daily", {})
            current = data.get("current", {})

            if not daily.get("temperature_2m_max"):
                return None

            wind_speed = int(round(daily['wind_speed_10m_max'][0]))
            wind_dir = int(round(daily.get('winddirection_10m_dominant', [0])[0]))
            directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                          "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
            wind_dir_text = directions[int((wind_dir + 11.25) / 22.5) % 16]

            # Rainfall so far today (from current)
            rainfall_so_far = round(current.get("precipitation", 0.0), 2)

            return {
                "temp_high": int(round(daily["temperature_2m_max"][0])),
                "temp_low": int(round(daily["temperature_2m_min"][0])),
                "wind": f"{wind_speed} mph {wind_dir_text}",
                "rainfall": rainfall_so_far
            }

        else:
            # Past date → use historical archive for accurate full day rainfall
            url = (
                "https://archive-api.open-meteo.com/v1/archive"
                f"?latitude={lat}&longitude={lon}"
                f"&start_date={target_date.isoformat()}&end_date={target_date.isoformat()}"
                "&daily=temperature_2m_max,temperature_2m_min,wind_speed_10m_max,winddirection_10m_dominant,precipitation_sum"
                "&timezone=America/Chicago&temperature_unit=fahrenheit&wind_speed_unit=mph"
            )
            resp = requests.get(url, timeout=12)
            if resp.status_code != 200:
                return None
            data = resp.json()
            daily = data.get("daily", {})
            if not daily.get("temperature_2m_max"):
                return None

            wind_speed = int(round(daily['wind_speed_10m_max'][0]))
            wind_dir = int(round(daily.get('winddirection_10m_dominant', [0])[0]))
            directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
                          "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
            wind_dir_text = directions[int((wind_dir + 11.25) / 22.5) % 16]

            rainfall = round(daily.get("precipitation_sum", [0.0])[0], 2)

            return {
                "temp_high": int(round(daily["temperature_2m_max"][0])),
                "temp_low": int(round(daily["temperature_2m_min"][0])),
                "wind": f"{wind_speed} mph {wind_dir_text}",
                "rainfall": rainfall
            }

    except Exception:
        return None


def render_species_input_grid(defaults: dict | None = None, key_prefix: str = "species") -> dict:
    if defaults is None:
        defaults = {sp: 0 for sp in SPECIES}
    counts = {}
    cols = st.columns(3)
    for i, sp in enumerate(SPECIES):
        with cols[i % 3]:
            counts[sp] = st.number_input(
                sp, min_value=0, max_value=200, value=int(defaults.get(sp, 0)),
                step=1, key=f"{key_prefix}_{sp}"
            )
    return counts


def generate_pdf_report(period_label: str, df: pd.DataFrame, species_totals: dict, output_path: Path):
    """Create professional PDF report with logo."""
    pdf = FPDF()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # Logo header
    if LOGO_PATH.exists():
        pdf.image(str(LOGO_PATH), x=10, y=8, w=35)

    pdf.set_font("Helvetica", "B", 22)
    pdf.set_text_color(30, 60, 90)
    pdf.cell(0, 12, "DD Hunt Report", ln=True, align="C")
    pdf.set_font("Helvetica", "", 12)
    pdf.set_text_color(80, 80, 80)
    pdf.cell(0, 7, f"Period: {period_label}", ln=True, align="C")
    pdf.ln(8)

    # Summary box
    pdf.set_fill_color(240, 248, 255)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(0, 8, "Season Highlights", ln=True, fill=True)
    pdf.set_font("Helvetica", "", 11)
    total_ducks = int(df["daily_total"].sum()) if not df.empty else 0
    pdf.cell(0, 7, f"- Total Ducks Harvested: {total_ducks:,}", ln=True)
    pdf.cell(0, 7, f"- Hunting Days: {len(df)}", ln=True)
    if not df.empty:
        pdf.cell(0, 7, f"- Average Daily Bag: {df['daily_total'].mean():.1f}", ln=True)
        pdf.cell(0, 7, f"- Best Day: {int(df['daily_total'].max())} ducks", ln=True)
    pdf.ln(6)

    # Hunt table
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Detailed Hunt Log", ln=True)
    pdf.set_font("Helvetica", "B", 9)
    col_widths = [22, 45, 14, 55, 40]
    headers = ["Date", "Location", "Ducks", "Hunters", "Weather/River"]
    for i, h in enumerate(headers):
        pdf.cell(col_widths[i], 7, h, border=1, align="C")
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for _, row in df.iterrows():
        pdf.cell(col_widths[0], 6, str(row["date"]), border=1)
        pdf.cell(col_widths[1], 6, str(row.get("location", ""))[:28], border=1)
        pdf.cell(col_widths[2], 6, str(int(row["daily_total"])), border=1, align="C")
        hunters = str(row.get("hunters", ""))[:32]
        pdf.cell(col_widths[3], 6, hunters, border=1)
        weather = f"{row.get('wind','')[:18]} / {row.get('river_level','')[:12]}"
        pdf.cell(col_widths[4], 6, weather, border=1)
        pdf.ln()
    pdf.ln(5)

    # Species summary
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Species Breakdown", ln=True)
    pdf.set_font("Helvetica", "", 10)
    sorted_species = sorted(species_totals.items(), key=lambda x: -x[1])
    for sp, cnt in sorted_species:
        if cnt > 0:
            pdf.cell(0, 6, f"   {sp}: {cnt} birds", ln=True)
    pdf.ln(6)

    pdf.set_font("Helvetica", "I", 8)
    pdf.set_text_color(100, 100, 100)
    pdf.cell(0, 6, f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}  -  DD Hunt Tracker  -  Private Club Use Only", ln=True, align="C")

    pdf.output(str(output_path))
    return output_path


# ---------------- LOGIN SYSTEM ----------------
def show_login():
    # Professional centered logo + title
    st.markdown("<br><br>", unsafe_allow_html=True)
    
    if LOGO_PATH.exists():
        col1, col2, col3 = st.columns([1, 1.7, 1])
        with col2:
            st.image(str(LOGO_PATH), width=230)
            st.markdown(
                "<h2 style='text-align: center; margin-top: 12px; margin-bottom: 8px; font-weight: 600;'>DD Hunt Tracker</h2>",
                unsafe_allow_html=True
            )
    else:
        st.markdown("<h1 style='text-align:center;'>DD Hunt Tracker</h1>", unsafe_allow_html=True)
    
    st.markdown("### Secure Club Access")
    with st.form("login"):
        u = st.text_input("Username")
        p = st.text_input("Password", type="password")
        if st.form_submit_button("Login", use_container_width=True):
            USERS = {
                "admin": {"pw": "admin123", "role": "admin"},
                "viewer": {"pw": "viewer123", "role": "viewer"},
                "jeff": {"pw": "duckhunt", "role": "admin"},
                "andrew": {"pw": "andrew123", "role": "admin"},
                "kyle": {"pw": "kyle123", "role": "admin"},
                "adam": {"pw": "adam123", "role": "admin"},
                "justin": {"pw": "justin123", "role": "admin"},
                "mcguire": {"pw": "mcguire123", "role": "admin"},
            }
            if u in USERS and USERS[u]["pw"] == p:
                st.session_state.logged_in = True
                st.session_state.username = u
                st.session_state.role = USERS[u]["role"]
                st.rerun()
            else:
                st.error("Invalid login. Please use your assigned username and password.")
    st.info("Club accounts: jeff, andrew, kyle, adam, justin, mcguire (all full access)  •  Backup: admin/admin123 or viewer/viewer123")


# ---------------- MAIN APP ----------------
def main():
    init_db()

    # Session state for auth
    if "logged_in" not in st.session_state:
        st.session_state.logged_in = False
        st.session_state.username = ""
        st.session_state.role = ""

    if not st.session_state.logged_in:
        show_login()
        return

    # Sidebar
    st.sidebar.title("🦆 Navigation")
    pages = ["Dashboard", "Submit Daily Report", "View Hunt History", "Season Analytics", "Reports & Exports", "Manage Data"]
    page = st.sidebar.radio("Go to", pages, index=0)

    st.sidebar.divider()
    st.sidebar.success(f"👤 {st.session_state.username} ({st.session_state.role})")
    if st.sidebar.button("Logout", use_container_width=True):
        for k in ["logged_in", "username", "role"]:
            st.session_state[k] = "" if k != "logged_in" else False
        st.rerun()

    st.sidebar.caption("📱 Mobile-friendly • Install as PWA via browser menu for app-like experience on phone/tablet.")

    is_admin = st.session_state.role == "admin"

    # ========== DASHBOARD ==========
    if page == "Dashboard":
        # Header with logo on the right
        col1, col2 = st.columns([4, 1])
        with col1:
            st.title(APP_TITLE)
        with col2:
            if LOGO_PATH.exists():
                st.image(str(LOGO_PATH), width=90)
        st.caption("Your digital hunting journal • Track • Analyze • Remember every flight")

        df = get_all_hunts_df()
        if df.empty:
            st.info("No hunts yet. Submit your first report or load sample data in Manage Data.")
            return

        # Season filter
        seasons = sorted(df["season"].dropna().unique().tolist(), reverse=True)
        selected_season = st.selectbox("Filter by Season", ["All"] + seasons, index=0)
        if selected_season != "All":
            df = df[df["season"] == selected_season]

        total_ducks = int(df["daily_total"].sum())
        total_hunts = len(df)
        avg_daily = round(df["daily_total"].mean(), 1) if total_hunts else 0
        best_day = int(df["daily_total"].max()) if total_hunts else 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Total Ducks", f"{total_ducks:,}")
        c2.metric("Hunting Days", total_hunts)
        c3.metric("Avg Daily Bag", f"{avg_daily}")
        c4.metric("Best Day", best_day)

        st.divider()
        st.subheader("📊 Species Distribution")
        species_totals = {sp: int(df[SPECIES_COLS[sp]].sum()) for sp in SPECIES if SPECIES_COLS[sp] in df.columns}
        species_totals = {k: v for k, v in species_totals.items() if v > 0}
        if species_totals:
            fig = px.pie(values=list(species_totals.values()), names=list(species_totals.keys()),
                         title="Harvest by Species", color_discrete_sequence=px.colors.sequential.Blues_r)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.write("No species data.")

        st.subheader("🕒 Recent Activity")
        # Select relevant columns + all species counts
        species_cols_list = list(SPECIES_COLS.values())
        cols_to_get = ["date", "location", "daily_total", "hunters"] + species_cols_list
        recent = df.head(5)[cols_to_get].copy()

        def get_top_species(row):
            max_count = 0
            top_sp = ""
            for sp in SPECIES:
                col = SPECIES_COLS[sp]
                if col in row.index and pd.notna(row[col]) and row[col] > max_count:
                    max_count = row[col]
                    top_sp = sp
            if max_count > 0:
                return f"{top_sp} {int(max_count)}"
            return "—"

        recent["Top Species"] = recent.apply(get_top_species, axis=1)

        # Format date as "Dec. 6"
        recent["Date"] = pd.to_datetime(recent["date"]).dt.strftime("%b ") + pd.to_datetime(recent["date"]).dt.day.astype(str)

        # Final columns for display
        recent = recent[["Date", "location", "daily_total", "hunters", "Top Species"]].copy()
        recent.columns = ["Date", "Location", "Ducks", "Hunters", "Top Species"]
        st.dataframe(recent, use_container_width=True, hide_index=True)

    # ========== SUBMIT ==========
    elif page == "Submit Daily Report":
        if not is_admin:
            st.warning("🔒 Viewer mode — you can view but not submit new reports.")
            st.stop()

        st.title("📝 Submit Daily Hunt Report")
        st.caption("Matches your original paper form. Add photos of birds, scenery, or the crew!")

        # ==================== NEW AUTO-FILL SECTION ====================
        st.markdown("### 🌧️ Auto Weather")
        st.caption("Click the button below after choosing a date. Pulls River Level + Weather (including rainfall) for your farm from official sources.")

        col_date, col_btn = st.columns([1.8, 2.2])
        with col_date:
            hunt_date = st.date_input("Hunt Date *", value=date.today(), key="hunt_date_input")
        with col_btn:
            if st.button("🔄 Auto Weather", use_container_width=True, type="secondary"):
                with st.spinner("Contacting New Madrid gauge + Open-Meteo for your farm..."):
                    river_val = get_river_level_usgs(hunt_date)
                    weather = get_weather_open_meteo(hunt_date)

                    updated = []
                    if river_val:
                        st.session_state.auto_river_level = river_val
                        updated.append("River Level")
                    if weather:
                        st.session_state.auto_wind = weather["wind"]
                        st.session_state.auto_temp_high = weather["temp_high"]
                        st.session_state.auto_temp_low = weather["temp_low"]
                        st.session_state.auto_rainfall = weather.get("rainfall", 0.0)
                        updated.append("Weather (temp + wind + rainfall)")

                    if updated:
                        st.success(f"✅ {' + '.join(updated)} loaded from {hunt_date}. You can still edit the values below.")
                    else:
                        st.warning("No data available for this date yet. Please enter the fields manually.")

        # Initialize session state keys
        if "auto_river_level" not in st.session_state:
            st.session_state.auto_river_level = ""
        if "auto_wind" not in st.session_state:
            st.session_state.auto_wind = ""
        if "auto_temp_high" not in st.session_state:
            st.session_state.auto_temp_high = 50
        if "auto_temp_low" not in st.session_state:
            st.session_state.auto_temp_low = 35
        if "auto_rainfall" not in st.session_state:
            st.session_state.auto_rainfall = 0.0

        # ==================== SUBMIT FORM ====================
        with st.form("submit_form", clear_on_submit=False):
            c2, c3 = st.columns(2)
            with c2:
                location = st.text_input("Location / Blind", placeholder="North Levee, South Blind, Main Pond...")
            with c3:
                wind = st.text_input("Wind", value=st.session_state.auto_wind, placeholder="NW 10-15 mph gusty")

            c4, c5, c6 = st.columns(3)
            with c4:
                temp_high = st.number_input("High °F", -20, 110, value=st.session_state.auto_temp_high, step=1)
            with c5:
                temp_low = st.number_input("Low °F", -20, 110, value=st.session_state.auto_temp_low, step=1)
            with c6:
                river_level = st.text_input("River Level", value=st.session_state.auto_river_level, placeholder="Normal 7.2 ft - falling")

            # Rainfall row
            rainfall = st.number_input("Rainfall (inches)", min_value=0.0, max_value=20.0, value=float(st.session_state.auto_rainfall), step=0.1, format="%.2f")

            st.subheader("👥 Hunters (one per line)")
            hunters_text = st.text_area("Hunters", height=80, placeholder="Jeff Utley\nMike Thompson")

            st.subheader("🦆 Species Harvested")
            species_counts = render_species_input_grid(key_prefix="new")

            st.subheader("📝 Notes")
            notes = st.text_area("Notes / Comments", height=100, placeholder="Memorable moments, conditions...")

            st.subheader("📷 Attach Photos (birds, scenery, group)")
            photos = st.file_uploader("Upload images (jpg/png)", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
            photo_captions = []
            if photos:
                for i, p in enumerate(photos):
                    cap = st.text_input(f"Caption for {p.name}", key=f"cap_new_{i}", placeholder="Optional caption...")
                    photo_captions.append(cap)

            submitted = st.form_submit_button("✅ SUBMIT HUNT REPORT", use_container_width=True, type="primary")

        if submitted:
            hunters_list = [h.strip() for h in hunters_text.split("\n") if h.strip()]
            data = {
                "date": st.session_state.hunt_date_input.isoformat(),
                "location": location.strip(),
                "wind": wind.strip(),
                "temp_high": int(temp_high),
                "temp_low": int(temp_low),
                "river_level": river_level.strip(),
                "rainfall": float(rainfall),
                "notes": notes.strip()
            }
            for sp in SPECIES:
                data[SPECIES_COLS[sp]] = species_counts.get(sp, 0)

            try:
                new_id = add_hunt(data, hunters_list)
                if photos:
                    add_photos_to_hunt(new_id, photos, photo_captions)
                daily_total = sum(species_counts.values())
                st.success(f"🎉 Hunt #{new_id} saved! Daily total: {daily_total} ducks")

                # Flying ducks animation (replaces default balloons)
                st.markdown("""
<style>
.duck-row {
    height: 95px;
    position: relative;
    overflow: hidden;
    margin: 8px 0 4px 0;
}
.duck {
    position: absolute;
    font-size: 44px;
    animation: fly-across 2.6s linear forwards;
    opacity: 0.92;
    filter: drop-shadow(1px 2px 2px rgba(0,0,0,0.15));
}
@keyframes fly-across {
    0%   { left: -70px; transform: translateY(12px) rotate(-7deg); }
    100% { left: 108%; transform: translateY(-18px) rotate(5deg); }
}
</style>
<div class="duck-row">
    <div class="duck" style="animation-delay: 0s; top: 5px;">🦆</div>
    <div class="duck" style="animation-delay: 0.5s; top: 35px; font-size: 36px;">🦆</div>
    <div class="duck" style="animation-delay: 1.05s; top: 15px; font-size: 40px;">🦆</div>
</div>
""", unsafe_allow_html=True)

                # Clear auto-fill values after successful submit so next entry starts fresh
                for key in ["auto_river_level", "auto_wind", "auto_temp_high", "auto_temp_low"]:
                    if key in st.session_state:
                        del st.session_state[key]
            except Exception as e:
                st.error(f"Save failed: {e}")

    # ========== HISTORY ==========
    elif page == "View Hunt History":
        st.title("📜 Hunt History")
        st.caption("Browse, search, edit, or delete entries. Photos appear in the details view.")

        df = get_all_hunts_df()
        if df.empty:
            st.info("No hunts logged yet.")
            return

        # Filters
        with st.expander("🔍 Filters"):
            seasons = ["All"] + sorted(df["season"].dropna().unique().tolist(), reverse=True)
            sf = st.selectbox("Season", seasons, index=0)
            loc_q = st.text_input("Location contains...")
            if sf != "All":
                df = df[df["season"] == sf]
            if loc_q:
                df = df[df["location"].str.contains(loc_q, case=False, na=False)]

        display_cols = ["id", "date", "season", "location", "daily_total", "hunters", "wind", "river_level"]
        disp = df[display_cols].copy()
        disp.columns = ["ID", "Date", "Season", "Location", "Ducks", "Hunters", "Wind", "River"]
        st.dataframe(disp, use_container_width=True, hide_index=True)

        csv = df.to_csv(index=False)
        st.download_button("⬇️ Download CSV", csv, f"hunt_log_{datetime.now().strftime('%Y%m%d')}.csv", "text/csv")

        st.divider()
        st.subheader("🔧 Manage Selected Hunt")

        if not df.empty:
            sel_id = st.selectbox("Select Hunt", df["id"].tolist(),
                                  format_func=lambda x: f"#{x} • {df[df.id==x].date.values[0]} • {df[df.id==x].location.values[0] or 'No loc'} ({int(df[df.id==x].daily_total.values[0])} ducks)")

            if sel_id:
                details = get_hunt_details(sel_id)
                photos = get_hunt_photos(sel_id)

                tab1, tab2, tab3 = st.tabs(["📋 Details & Photos", "✏️ Edit", "🗑️ Delete"])

                with tab1:
                    st.markdown(f"**Hunt #{sel_id}** — {details.get('date')} ({details.get('season', 'N/A')})")
                    st.write(f"**Location:** {details.get('location') or '—'}")
                    st.write(f"**Weather:** {details.get('wind') or '—'} | High {details.get('temp_high')}° / Low {details.get('temp_low')}° | River: {details.get('river_level') or '—'}")
                    st.write(f"**Hunters:** {', '.join(details.get('hunters', [])) or '—'}")
                    if details.get("notes"):
                        st.info(details["notes"])

                    if photos:
                        st.subheader("📷 Photos from this hunt")
                        cols = st.columns(min(3, len(photos)))
                        for idx, ph in enumerate(photos):
                            with cols[idx % 3]:
                                if Path(ph["full_path"]).exists():
                                    st.image(ph["full_path"], caption=ph["caption"] or ph["filename"][:30], width=180)
                                if is_admin and st.button(f"🗑️ Delete photo #{ph['id']}", key=f"delph_{ph['id']}"):
                                    delete_photo(ph["id"])
                                    st.rerun()

                with tab2:
                    if not is_admin:
                        st.warning("Viewers cannot edit.")
                    else:
                        with st.form(f"edit_{sel_id}"):
                            e_date = st.date_input("Date", value=datetime.strptime(details["date"], "%Y-%m-%d").date())
                            e_loc = st.text_input("Location", value=details.get("location") or "")
                            e_wind = st.text_input("Wind", value=details.get("wind") or "")
                            ec1, ec2, ec3 = st.columns(3)
                            with ec1: e_high = st.number_input("High °F", value=details.get("temp_high") or 50)
                            with ec2: e_low = st.number_input("Low °F", value=details.get("temp_low") or 35)
                            with ec3: e_river = st.text_input("River Level", value=details.get("river_level") or "")
                            e_hunters = st.text_area("Hunters (one per line)", value="\n".join(details.get("hunters", [])), height=70)
                            e_notes = st.text_area("Notes", value=details.get("notes") or "", height=80)

                            st.subheader("Species Counts")
                            curr_counts = {sp: details.get(SPECIES_COLS[sp], 0) for sp in SPECIES}
                            e_counts = render_species_input_grid(defaults=curr_counts, key_prefix=f"edit_{sel_id}")

                            st.subheader("Add more photos (optional)")
                            new_photos = st.file_uploader("New photos", type=["jpg","png"], accept_multiple_files=True, key=f"newph_{sel_id}")
                            new_caps = []
                            if new_photos:
                                for i, p in enumerate(new_photos):
                                    new_caps.append(st.text_input(f"Caption for {p.name}", key=f"newcap_{sel_id}_{i}"))

                            if st.form_submit_button("💾 Save Changes"):
                                e_h_list = [h.strip() for h in e_hunters.split("\n") if h.strip()]
                                edit_data = {
                                    "date": e_date.isoformat(), "location": e_loc.strip(), "wind": e_wind.strip(),
                                    "temp_high": int(e_high), "temp_low": int(e_low),
                                    "river_level": e_river.strip(), "notes": e_notes.strip()
                                }
                                for sp in SPECIES:
                                    edit_data[SPECIES_COLS[sp]] = e_counts.get(sp, 0)
                                try:
                                    update_hunt(sel_id, edit_data, e_h_list)
                                    if new_photos:
                                        add_photos_to_hunt(sel_id, new_photos, new_caps)
                                    st.success("Updated!")
                                    st.rerun()
                                except Exception as ex:
                                    st.error(f"Update error: {ex}")

                with tab3:
                    if not is_admin:
                        st.warning("Viewers cannot delete.")
                    else:
                        st.error("Permanent delete!")
                        if st.checkbox(f"Confirm delete Hunt #{sel_id} and all its photos"):
                            if st.button("🗑️ DELETE HUNT", type="secondary"):
                                delete_hunt(sel_id)
                                st.success("Deleted.")
                                st.rerun()

    # ========== ANALYTICS ==========
    elif page == "Season Analytics":
        st.title("📈 Season Analytics & Trends")
        df = get_all_hunts_df()
        if df.empty:
            st.info("Add hunts to see trends.")
            return

        seasons = ["All"] + sorted(df["season"].dropna().unique().tolist(), reverse=True)
        sel_season = st.selectbox("Season", seasons, index=0)
        if sel_season != "All":
            df = df[df["season"] == sel_season]

        st.subheader("Daily Bag Trend")
        df_sorted = df.sort_values("date")
        fig = px.line(df_sorted, x="date", y="daily_total", markers=True, hover_data=["location"],
                      title="Ducks per Hunt Day")
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("🏆 Top 5 Days")
        top = df.nlargest(5, "daily_total")[["date", "location", "daily_total", "hunters"]].copy()
        top["date"] = pd.to_datetime(top["date"]).dt.strftime("%b. %d")
        st.dataframe(top, hide_index=True, use_container_width=True)

        st.subheader("Weekly Totals")
        df_sorted["week_start"] = df_sorted["date"] - pd.to_timedelta(df_sorted["date"].dt.dayofweek, unit="D")
        weekly = df_sorted.groupby("week_start")["daily_total"].sum().reset_index()
        weekly = weekly.sort_values("week_start")
        weekly["Week"] = weekly["week_start"].dt.strftime("Week of %b %d")
        fig_bar = px.bar(weekly, x="Week", y="daily_total", text_auto=True, title="Ducks by Week")
        st.plotly_chart(fig_bar, use_container_width=True)

    # ========== REPORTS ==========
    elif page == "Reports & Exports":
        st.title("📊 Reports & Exports")
        st.caption("Generate professional summaries for the club or export data for eBird.")

        df = get_all_hunts_df()
        if df.empty:
            st.info("No data for reports yet.")
            return

        seasons = ["All"] + sorted(df["season"].dropna().unique().tolist(), reverse=True)
        sel_s = st.selectbox("Season for report", seasons, index=0)
        if sel_s != "All":
            df = df[df["season"] == sel_s]

        period_label = sel_s if sel_s != "All" else "All Seasons"

        c1, c2 = st.columns(2)
        with c1:
            if st.button("📄 Generate PDF Club Report", use_container_width=True):
                species_tot = {sp: int(df[SPECIES_COLS[sp]].sum()) for sp in SPECIES}
                pdf_path = BASE_DIR / f"DD_Lodge_Report_{datetime.now().strftime('%Y%m%d_%H%M')}.pdf"
                generate_pdf_report(period_label, df, species_tot, pdf_path)
                with open(pdf_path, "rb") as f:
                    st.download_button("⬇️ Download PDF", f.read(), pdf_path.name, "application/pdf", use_container_width=True)

        with c2:
            if st.button("🐦 Export eBird-style Checklist CSV", use_container_width=True):
                lines = ["Common Name,Count,Date,Location,Protocol,Notes\n"]
                for _, r in df.iterrows():
                    for sp in SPECIES:
                        cnt = r.get(SPECIES_COLS[sp], 0)
                        if cnt > 0:
                            lines.append(f'"{sp}",{cnt},{r["date"]},"{r.get("location","")}","Stationary","From DD Lodge hunt log"\n')
                csv_text = "".join(lines)
                st.download_button("⬇️ Download eBird CSV", csv_text, "dd_lodge_ebird_checklist.csv", "text/csv", use_container_width=True)

        st.info("**eBird tip:** The CSV is formatted for easy import into eBird. Adjust 'Protocol' or add exact location coordinates as needed before uploading.")

        st.subheader("Quick Period Stats")
        if not df.empty:
            st.metric("Ducks in Selected Period", int(df["daily_total"].sum()))
            st.metric("Hunts", len(df))

    # ========== MANAGE ==========
    elif page == "Manage Data":
        if not is_admin:
            st.warning("🔒 Admin access required for data management.")
            st.stop()

        st.title("⚙️ Manage Data")
        st.subheader("Demo Data")
        if st.button("🧪 Load Sample Data (resets if exists)"):
            conn = get_db_connection()
            c = conn.cursor()
            c.execute("DELETE FROM hunt_photos")
            c.execute("DELETE FROM hunt_hunters")
            c.execute("DELETE FROM hunts")
            conn.commit()
            conn.close()
            for f in UPLOAD_DIR.glob("*"):
                try: f.unlink()
                except: pass
            if load_sample_data():
                st.success("Sample data loaded with 4 hunts!")
            st.rerun()

        st.subheader("Danger Zone")
        if st.button("🗑️ Clear ALL Data (keeps tables)"):
            if st.checkbox("Type YES to confirm"):
                conn = get_db_connection()
                c = conn.cursor()
                c.execute("DELETE FROM hunt_photos")
                c.execute("DELETE FROM hunt_hunters")
                c.execute("DELETE FROM hunts")
                conn.commit()
                conn.close()
                for f in UPLOAD_DIR.glob("*"):
                    try: f.unlink()
                    except: pass
                st.success("All data cleared.")
                st.rerun()

        st.subheader("Backup")
        st.code(f"Database: {DB_PATH}\nUploads folder: {UPLOAD_DIR}")
        st.info("Copy the entire duck_hunt_tracker folder (including uploads/ and .db) to backup or share with club members. All photos and data travel together.")

        st.subheader("About DD Hunt Tracker")
        st.write("""
        Built to replace paper logs with modern tracking, photos, reports, and role-based access.

        - Matches your original form fields exactly  
        - Photos per hunt (birds, scenery, crew)  
        - Automatic season detection & filtering (2025-2026 etc.)  
        - Multi-user login (admin full control, viewer read-only)  
        - PDF club reports + eBird export  
        - Mobile-friendly PWA installable on phones/tablets  
        - 100% private — everything stays in your shared folder

        Logo proudly displayed: DD Lodge Entrance Logo.
        """)


if __name__ == "__main__":
    main()
