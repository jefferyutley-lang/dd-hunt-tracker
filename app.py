import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import date
import os
import requests
from io import BytesIO
from fpdf import FPDF

# ====================== SUPABASE ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fkkmjfzjhoigqwimmdcq.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZra21qZnpqaG9pZ3F3aW1tZGNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI2MTg3MzEsImV4cCI6MjA5ODE5NDczMX0.2wy4sQ0FsVdjCqcPQE1_m-vxAD-mRVsAVoyzOja1Qso")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ====================== SESSION STATE ======================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

# ====================== WEATHER ======================
def get_weather_data(hunt_date):
    lat = 36.68218
    lon = -89.37869
    date_str = hunt_date.strftime("%Y-%m-%d")
    today = date.today()

    try:
        if hunt_date == today:
            url = "https://api.open-meteo.com/v1/forecast"
            params = {"latitude": lat, "longitude": lon, "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant"], "timezone": "America/Chicago", "forecast_days": 1}
        else:
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {"latitude": lat, "longitude": lon, "start_date": date_str, "end_date": date_str, "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant"], "timezone": "America/Chicago"}

        response = requests.get(url, params=params, timeout=10)
        data = response.json()
        daily = data.get("daily", {})

        high = int(round(daily.get("temperature_2m_max", [50])[0] or 50))
        low = int(round(daily.get("temperature_2m_min", [35])[0] or 35))
        rain = float(round(daily.get("precipitation_sum", [0])[0] or 0, 2))
        wind_speed = int(round(daily.get("wind_speed_10m_max", [0])[0] or 0))
        wind_dir = daily.get("wind_direction_10m_dominant", [None])[0]

        wind_text = f"{wind_speed} mph"
        if wind_dir:
            directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
            idx = round(wind_dir / 22.5) % 16
            wind_text = f"{wind_speed} mph {directions[idx]}"

        return {"high_temp": high, "low_temp": low, "rainfall": rain, "wind": wind_text}
    except:
        return None

# ====================== LOGIN ======================
def show_login():
    st.title("DD Hunt Tracker")
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            try:
                supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.logged_in = True
                st.session_state.username = email.split("@")[0]
                st.rerun()
            except:
                st.error("Invalid login")

if not st.session_state.logged_in:
    show_login()
    st.stop()

# ====================== MAIN APP ======================
st.sidebar.image("dd_logo.png", width=160)
st.sidebar.title("DD Hunt Tracker")
st.sidebar.write(f"Logged in as: **{st.session_state.username}**")
if st.sidebar.button("Logout"):
    st.session_state.logged_in = False
    st.rerun()

tab1, tab2, tab3, tab4 = st.tabs(["Dashboard", "Submit Daily Report", "View Hunt History", "Season Analytics"])

# Dashboard
with tab1:
    st.header("Dashboard")
    st.write("Welcome to DD Hunt Tracker")

# Submit Daily Report
with tab2:
    st.header("Submit Daily Hunt Report")
    hunt_date = st.date_input("Hunt Date", value=date.today())

    if "last_hunt_date" not in st.session_state:
        st.session_state.last_hunt_date = None

    if hunt_date != st.session_state.last_hunt_date:
        st.session_state.last_hunt_date = hunt_date
        weather = get_weather_data(hunt_date)
        if weather:
            st.session_state.auto_high = weather["high_temp"]
            st.session_state.auto_low = weather["low_temp"]
            st.session_state.auto_rainfall = float(weather["rainfall"])
            st.session_state.auto_wind = weather["wind"]
            st.success(f"Weather loaded for {hunt_date.strftime('%b %d, %Y')}")
        else:
            st.warning("Could not load weather for this date.")

    with st.form("submit_hunt"):
        location = st.text_input("Location / Blind")
        wind = st.text_input("Wind", value=st.session_state.get("auto_wind", ""))
        high_temp = st.number_input("High °F", value=st.session_state.get("auto_high", 50))
        low_temp = st.number_input("Low °F", value=st.session_state.get("auto_low", 35))
        river_level = st.text_input("River Level")
        rainfall = st.number_input("Rainfall (inches)", value=st.session_state.get("auto_rainfall", 0.0), step=0.1)

        hunters = st.text_area("Hunters (one per line)")

        st.subheader("Species Harvested")
        col1, col2 = st.columns(2)
        with col1:
            mallard = st.number_input("Mallard", value=0)
            gadwall = st.number_input("Gadwall", value=0)
            teal = st.number_input("Teal", value=0)
            pintail = st.number_input("Pintail", value=0)
            wood_duck = st.number_input("Wood Duck", value=0)
        with col2:
            widgeon = st.number_input("Widgeon", value=0)
            shoveler = st.number_input("Shoveler", value=0)
            canvasback = st.number_input("Canvasback", value=0)
            redhead = st.number_input("Redhead", value=0)
            divers = st.number_input("Divers", value=0)
            geese = st.number_input("Geese", value=0)

        total_ducks = mallard + gadwall + teal + pintail + wood_duck + widgeon + shoveler + canvasback + redhead + divers + geese
        st.metric("Total Ducks", total_ducks)

        notes = st.text_area("Notes")

        if st.form_submit_button("Submit Hunt"):
            data = {
                "date": str(hunt_date),
                "location": location,
                "wind": wind,
                "high_temp": high_temp,
                "low_temp": low_temp,
                "river_level": river_level,
                "rainfall": rainfall,
                "hunters": hunters,
                "mallard": mallard,
                "gadwall": gadwall,
                "teal": teal,
                "pintail": pintail,
                "wood_duck": wood_duck,
                "widgeon": widgeon,
                "shoveler": shoveler,
                "canvasback": canvasback,
                "redhead": redhead,
                "divers": divers,
                "geese": geese,
                "notes": notes,
                "season": "2025-2026",
                "created_by": st.session_state.username
            }
            supabase.table("hunts").insert(data).execute()
            st.success("Hunt submitted successfully!")

# View Hunt History
with tab3:
    st.header("View Hunt History")
    st.info("Hunt history, edit, delete, and export features coming in next update.")

with tab4:
    st.header("Season Analytics")
    st.write("Coming soon...")
