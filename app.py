import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import datetime, date
import os

# ====================== SUPABASE CONNECTION ======================
SUPABASE_URL = os.getenv("SUPABASE_URL", "https://fkkmjfzjhoigqwimmdcq.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImZra21qZnpqaG9pZ3F3aW1tZGNxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODI2MTg3MzEsImV4cCI6MjA5ODE5NDczMX0.2wy4sQ0FsVdjCqcPQE1_m-vxAD-mRVsAVoyzOja1Qso")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# ====================== SESSION STATE ======================
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

# ====================== LOGIN ======================
def show_login():
    st.title("DD Hunt Tracker")
    st.subheader("Secure Club Access")

    with st.form("login_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        if st.form_submit_button("Login"):
            try:
                response = supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.logged_in = True
                st.session_state.username = email.split("@")[0]
                st.rerun()
            except:
                st.error("Invalid email or password")

# ====================== MAIN APP ======================
def main_app():
    st.sidebar.title("DD Hunt Tracker")
    st.sidebar.write(f"Logged in as: **{st.session_state.username}**")
    if st.sidebar.button("Logout"):
        st.session_state.logged_in = False
        st.rerun()

    tab1, tab2, tab3, tab4 = st.tabs(["Dashboard", "Submit Daily Report", "View Hunt History", "Season Analytics"])

    # ====================== DASHBOARD ======================
    with tab1:
        st.header("Dashboard")
        st.write("Welcome back!")

    # ====================== SUBMIT DAILY REPORT ======================
    with tab2:
        st.header("Submit Daily Hunt Report")

        hunt_date = st.date_input("Hunt Date", value=date.today())

        # ====================== AUTO LOAD WEATHER WHEN DATE CHANGES ======================
        if "last_date" not in st.session_state:
            st.session_state.last_date = None

        if hunt_date != st.session_state.last_date:
            st.session_state.last_date = hunt_date
            # Here we would normally call the weather API
            # For now we'll just show a message
            st.info(f"Weather data would load for {hunt_date} (API integration coming next)")

        with st.form("submit_form"):
            location = st.text_input("Location / Blind")
            wind = st.text_input("Wind")
            high_temp = st.number_input("High °F", value=50)
            low_temp = st.number_input("Low °F", value=35)
            river_level = st.text_input("River Level")
            rainfall = st.number_input("Rainfall (inches)", value=0.0, step=0.1)

            hunters = st.text_area("Hunters (one per line)")

            st.subheader("Species")
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

    # ====================== VIEW HUNT HISTORY ======================
    with tab3:
        st.header("View Hunt History")
        try:
            response = supabase.table("hunts").select("*").order("date", desc=True).execute()
            if response.data:
                df = pd.DataFrame(response.data)
                st.dataframe(df)
            else:
                st.info("No hunts yet.")
        except Exception as e:
            st.error(str(e))

    # ====================== SEASON ANALYTICS ======================
    with tab4:
        st.header("Season Analytics")
        st.subheader("Weekly Totals")
        try:
            response = supabase.table("hunts").select("*").execute()
            if response.data:
                df = pd.DataFrame(response.data)
                df["date"] = pd.to_datetime(df["date"])
                df["week_start"] = df["date"] - pd.to_timedelta(df["date"].dt.dayofweek, unit="D")
                weekly = df.groupby("week_start")["daily_total"].sum().reset_index()
                weekly = weekly.sort_values("week_start")
                weekly["Week"] = weekly["week_start"].dt.strftime("Week of %b %d")
                st.bar_chart(weekly.set_index("Week")["daily_total"])
            else:
                st.info("No data yet.")
        except Exception as e:
            st.error(f"Error: {e}")

# ====================== RUN APP ======================
if st.session_state.logged_in:
    main_app()
else:
    show_login()
