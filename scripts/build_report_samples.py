"""Build sample report PDFs from a JSON snapshot of the Supabase tables (prototype only).

Usage: python scripts/build_report_samples.py SNAPSHOT.json OUT_DIR
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import reports as R  # noqa: E402

snap, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
d = json.loads(snap.read_text())

hunts = R.hunts_df_from_rows(d.get("hunts") or [])
sight = {}
for s in d.get("sightings") or []:
    sight.setdefault(s["survey_id"], [])
    if s["species"] not in sight[s["survey_id"]]:
        sight[s["survey_id"]].append(s["species"])
surveys = pd.DataFrame([{"id": s["id"], "survey_date": s["observed_at"], "location": s["location"],
                         "observers": s.get("observers"), "notes": s.get("notes"),
                         "species_seen": ", ".join(sight.get(s["id"], []))} for s in d.get("surveys") or []])
plans = {}
for p in d.get("farm_plans") or []:
    plans.setdefault(p["year"], {})[p["location"]] = p.get("notes") or ""

today = date.today()
overrides = {(r["year"], r["month"]): r["inches"] for r in (d.get("farm_rainfall") or [])}
rain = {}
for y in range(today.year - 2, today.year + 1):
    end = min(date(y, 10, 31), today - timedelta(days=1))
    auto = {}
    try:
        j = requests.get("https://archive-api.open-meteo.com/v1/archive?latitude=36.68218&longitude=-89.37869"
                         f"&start_date={y}-07-01&end_date={end}&daily=precipitation_sum&precipitation_unit=inch"
                         "&timezone=America%2FChicago", timeout=20).json()["daily"]
        for t, v in zip(j["time"], j["precipitation_sum"]):
            auto[int(t[5:7])] = auto.get(int(t[5:7]), 0) + (v or 0)
    except Exception as e:
        print("rain fetch failed", y, e)
    vals, src = {}, {}
    for m in R.RAIN_MONTHS:
        if (y, m) in overrides:
            vals[m], src[m] = overrides[(y, m)], "gauge"
        elif m in auto:
            vals[m], src[m] = round(auto[m], 2), "auto"
    rain[y] = {"vals": vals, "src": src}

data = {"hunts": hunts, "surveys": surveys, "farm_activities": d.get("farm_activities") or [], "farm_plans": plans,
        "well_runs": d.get("well_runs") or [], "rainfall": rain, "assets": d.get("assets") or [],
        "maintenance_log": d.get("maintenance_log") or [], "farm_year": today.year}

(out / "club-report.pdf").write_bytes(R.build_club_report(hunts, "2026-2027"))
(out / "hunt-history-report.pdf").write_bytes(R.build_history_report(hunts, "All"))
(out / "ebird-checklist-report.pdf").write_bytes(R.build_ebird_report(hunts, "All"))
(out / "hunt-history-with-charts.xlsx").write_bytes(R.build_excel_workbook(hunts, "All"))
(out / "dd_lodge_ebird_checklist.csv").write_text(R.ebird_csv(hunts))
pdf, skipped = R.build_master_report(data, "All")
(out / "master-report.pdf").write_bytes(pdf)
print("skipped:", skipped)
print("rain:", {y: v["vals"] for y, v in rain.items()})
