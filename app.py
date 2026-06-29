import streamlit as st
from supabase import create_client, Client
import pandas as pd
from datetime import date, datetime, timedelta
import os
import requests
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================== CONFIGURATION ====================
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("❌ Missing Supabase credentials. Please set SUPABASE_URL and SUPABASE_KEY environment variables.")
    st.stop()

try:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
except Exception as e:
    st.error(f"❌ Failed to connect to Supabase: {str(e)}")
    logger.error(f"Supabase connection error: {str(e)}")
    st.stop()

# Page configuration
st.set_page_config(page_title="DD Hunt Tracker", page_icon="🦆", layout="wide")

# Initialize session state
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
    st.session_state.username = ""

SPECIES = ["mallard", "gadwall", "teal", "pintail", "wood_duck", "widgeon", "shoveler", "canvasback", "redhead", "divers", "geese"]

# ==================== WEATHER FUNCTION ====================
@st.cache_data(ttl=3600)
def get_weather_data(hunt_date):
    """
    Fetch weather data from Open-Meteo API.
    Uses forecast API for today, archive API for past dates.
    """
    lat = 36.68218
    lon = -89.37869
    
    try:
        if hunt_date == date.today():
            url = "https://api.open-meteo.com/v1/forecast"
            params = {
                "latitude": lat,
                "longitude": lon,
                "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant"],
                "timezone": "America/Chicago",
                "forecast_days": 1
            }
        else:
            url = "https://archive-api.open-meteo.com/v1/archive"
            params = {
                "latitude": lat,
                "longitude": lon,
                "start_date": hunt_date.strftime("%Y-%m-%d"),
                "end_date": hunt_date.strftime("%Y-%m-%d"),
                "daily": ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "wind_speed_10m_max", "wind_direction_10m_dominant"],
                "timezone": "America/Chicago"
            }
        
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        daily = data.get("daily", {})
        
        high = int(round(daily.get("temperature_2m_max", [55])[0] or 55))
        low = int(round(daily.get("temperature_2m_min", [40])[0] or 40))
        rain = float(round(daily.get("precipitation_sum", [0])[0] or 0, 2))
        wind_speed = int(round(daily.get("wind_speed_10m_max", [0])[0] or 0))
        wind_dir = daily.get("wind_direction_10m_dominant", [0])[0] or 0
        
        directions = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
        wind_text = f"{wind_speed} mph {directions[int((wind_dir % 360) / 22.5) % 16]}"
        
        return {"high_temp": high, "low_temp": low, "rainfall": rain, "wind": wind_text}
    except requests.RequestException as e:
        logger.warning(f"Weather API error: {str(e)}")
        return {"high_temp": 55, "low_temp": 40, "rainfall": 0.0, "wind": "N/A"}
    except Exception as e:
        logger.error(f"Unexpected weather error: {str(e)}")
        return {"high_temp": 55, "low_temp": 40, "rainfall": 0.0, "wind": "N/A"}

# ==================== AUTHENTICATION ====================
def show_login():
    """Display login form"""
    st.title("🦆 DD Hunt Tracker")
    st.write("Track your duck hunting season")
    
    with st.form("login"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")
        
        if submit:
            if not email or not password:
                st.error("❌ Please enter both email and password")
                return
            
            try:
                supabase.auth.sign_in_with_password({"email": email, "password": password})
                st.session_state.logged_in = True
                st.session_state.username = email.split("@")[0]
                st.success("✅ Login successful!")
                st.rerun()
            except Exception as e:
                logger.error(f"Login error: {str(e)}")
                st.error("❌ Invalid email or password")

def logout():
    """Handle logout"""
    st.session_state.logged_in = False
    st.session_state.username = ""
    st.rerun()

if not st.session_state.logged_in:
    show_login()
    st.stop()

# ==================== SIDEBAR ====================
with st.sidebar:
    try:
        st.image("dd_logo.png", width=160)
    except:
        st.title("🦆 DD Hunt Tracker")
    
    st.write(f"👤 Logged in as: **{st.session_state.username}**")
    if st.button("🚪 Logout", use_container_width=True):
        logout()
    
    st.divider()
    st.write("**Season:** 2025-2026")

# ==================== MAIN APP ====================
tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard", "📝 Submit Report", "📋 Hunt History", "📈 Analytics"])

# ==================== TAB 1: DASHBOARD ====================
with tab1:
    st.header("Dashboard")
    
    try:
        response = supabase.table("hunts").select("*").order("date", desc=True).limit(10).execute()
        
        if response.data:
            df = pd.DataFrame(response.data)
            df["date"] = pd.to_datetime(df["date"])
            df["Date"] = df["date"].dt.strftime("%b %d, %Y")
            
            # Calculate totals
            total_hunts = len(df)
            total_ducks = df[SPECIES].sum().sum()
            avg_per_hunt = int(total_ducks / total_hunts) if total_hunts > 0 else 0
            
            # Display metrics
            col1, col2, col3 = st.columns(3)
            col1.metric("🦆 Total Ducks (10 hunts)", int(total_ducks))
            col2.metric("🎯 Hunt Count", total_hunts)
            col3.metric("📊 Avg per Hunt", avg_per_hunt)
            
            st.divider()
            st.subheader("Recent Hunts")
            
            # Calculate highest species for each hunt
            df["Total"] = df[SPECIES].sum(axis=1)
            df["Highest Species"] = df[SPECIES].apply(
                lambda row: row.idxmax().replace("_", " ").title() + f" ({int(row.max())})" if row.max() > 0 else "None",
                axis=1
            )
            
            display_df = df[["Date", "location", "Highest Species", "Total", "river_level"]].copy()
            display_df.columns = ["Date", "Location", "Top Species", "Total Ducks", "River Level"]
            
            st.dataframe(display_df, use_container_width=True, hide_index=True)
        else:
            st.info("ℹ️ No hunts recorded yet. Start by submitting your first hunt report!")
    
    except Exception as e:
        logger.error(f"Dashboard error: {str(e)}")
        st.error(f"❌ Error loading dashboard: {str(e)}")

# ==================== TAB 2: SUBMIT REPORT ====================
with tab2:
    st.header("Submit Daily Hunt Report")
    
    hunt_date = st.date_input("Hunt Date", value=date.today())
    
    # Auto-load weather when date changes
    if "last_hunt_date" not in st.session_state or hunt_date != st.session_state.last_hunt_date:
        st.session_state.last_hunt_date = hunt_date
        weather = get_weather_data(hunt_date)
        if weather:
            st.session_state.auto_high = weather["high_temp"]
            st.session_state.auto_low = weather["low_temp"]
            st.session_state.auto_rainfall = float(weather["rainfall"])
            st.session_state.auto_wind = weather["wind"]
            st.success(f"☀️ Weather loaded for {hunt_date.strftime('%b %d, %Y')}")
    
    with st.form("submit_hunt"):
        col1, col2 = st.columns(2)
        
        with col1:
            location = st.text_input("Location / Blind", placeholder="e.g., North Blind, Grand Island")
            wind = st.text_input("Wind", value=st.session_state.get("auto_wind", ""), placeholder="e.g., 10 mph N")
            high_temp = st.number_input("High °F", value=st.session_state.get("auto_high", 55), min_value=-20, max_value=120)
            low_temp = st.number_input("Low °F", value=st.session_state.get("auto_low", 40), min_value=-20, max_value=120)
        
        with col2:
            river_level = st.text_input("River Level", placeholder="e.g., 2.5 ft")
            rainfall = st.number_input("Rainfall (inches)", value=st.session_state.get("auto_rainfall", 0.0), step=0.1, min_value=0.0)
            hunters = st.text_area("Hunters (one per line)", placeholder="Name each hunter on separate lines")
            notes = st.text_area("Notes", placeholder="Any additional observations...")
        
        st.subheader("Species Harvested")
        
        col1, col2, col3 = st.columns(3)
        species_counts = {}
        
        with col1:
            species_counts["mallard"] = st.number_input("Mallard", value=0, min_value=0)
            species_counts["gadwall"] = st.number_input("Gadwall", value=0, min_value=0)
            species_counts["teal"] = st.number_input("Teal", value=0, min_value=0)
            species_counts["pintail"] = st.number_input("Pintail", value=0, min_value=0)
        
        with col2:
            species_counts["wood_duck"] = st.number_input("Wood Duck", value=0, min_value=0)
            species_counts["widgeon"] = st.number_input("Widgeon", value=0, min_value=0)
            species_counts["shoveler"] = st.number_input("Shoveler", value=0, min_value=0)
            species_counts["canvasback"] = st.number_input("Canvasback", value=0, min_value=0)
        
        with col3:
            species_counts["redhead"] = st.number_input("Redhead", value=0, min_value=0)
            species_counts["divers"] = st.number_input("Divers", value=0, min_value=0)
            species_counts["geese"] = st.number_input("Geese", value=0, min_value=0)
        
        total_ducks = sum(species_counts.values())
        st.metric("🦆 Total Ducks", total_ducks)
        
        if st.form_submit_button("✅ Submit Hunt", use_container_width=True):
            if not location:
                st.error("❌ Location is required")
            else:
                try:
                    data = {
                        "date": str(hunt_date),
                        "location": location,
                        "wind": wind,
                        "high_temp": int(high_temp),
                        "low_temp": int(low_temp),
                        "river_level": river_level,
                        "rainfall": float(rainfall),
                        "hunters": hunters,
                        "notes": notes,
                        "season": "2025-2026",
                        "created_by": st.session_state.username,
                        **species_counts
                    }
                    supabase.table("hunts").insert(data).execute()
                    st.success("✅ Hunt submitted successfully!")
                    # Clear form by rerunning
                    st.rerun()
                except Exception as e:
                    logger.error(f"Submit hunt error: {str(e)}")
                    st.error(f"❌ Error submitting hunt: {str(e)}")

# ==================== TAB 3: HUNT HISTORY ====================
with tab3:
    st.header("Hunt History")
    
    try:
        response = supabase.table("hunts").select("*").order("date", desc=True).execute()
        
        if response.data:
            df = pd.DataFrame(response.data)
            df["date"] = pd.to_datetime(df["date"])
            
            # Filters
            col1, col2, col3 = st.columns(3)
            
            with col1:
                date_range = st.date_input(
                    "Date Range",
                    value=(df["date"].min().date(), df["date"].max().date()),
                    label_visibility="collapsed"
                )
                if isinstance(date_range, tuple) and len(date_range) == 2:
                    start_date, end_date = date_range
                    df = df[(df["date"].dt.date >= start_date) & (df["date"].dt.date <= end_date)]
            
            with col2:
                locations = ["All"] + sorted(df["location"].unique().tolist())
                selected_location = st.selectbox("Location", locations)
                if selected_location != "All":
                    df = df[df["location"] == selected_location]
            
            with col3:
                search_hunter = st.text_input("Search Hunter", placeholder="Hunter name...")
                if search_hunter:
                    df = df[df["hunters"].str.contains(search_hunter, case=False, na=False)]
            
            st.divider()
            
            if len(df) > 0:
                # Summary stats for filtered results
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Total Hunts", len(df))
                col2.metric("Total Ducks", int(df[SPECIES].sum().sum()))
                col3.metric("Avg per Hunt", int(df[SPECIES].sum().sum() / len(df)))
                col4.metric("Top Location", df["location"].value_counts().index[0] if len(df) > 0 else "N/A")
                
                st.divider()
                
                # Detailed table
                display_df = df.copy()
                display_df["Date"] = display_df["date"].dt.strftime("%b %d, %Y")
                display_df["Total"] = display_df[SPECIES].sum(axis=1)
                
                display_cols = ["Date", "location", "hunters", "Total", "high_temp", "low_temp", "rainfall", "river_level", "notes"]
                display_df = display_df[display_cols].rename(columns={
                    "location": "Location",
                    "hunters": "Hunters",
                    "high_temp": "High °F",
                    "low_temp": "Low °F",
                    "rainfall": "Rain (in)",
                    "river_level": "River Level",
                    "notes": "Notes"
                })
                
                st.dataframe(display_df, use_container_width=True, hide_index=True)
                
                # Download button
                csv = display_df.to_csv(index=False)
                st.download_button(
                    label="📥 Download as CSV",
                    data=csv,
                    file_name=f"hunt_history_{date.today()}.csv",
                    mime="text/csv"
                )
            else:
                st.info("ℹ️ No hunts match your filters")
        else:
            st.info("ℹ️ No hunt history yet")
    
    except Exception as e:
        logger.error(f"Hunt history error: {str(e)}")
        st.error(f"❌ Error loading hunt history: {str(e)}")

# ==================== TAB 4: SEASON ANALYTICS ====================
with tab4:
    st.header("Season Analytics")
    
    try:
        response = supabase.table("hunts").select("*").execute()
        
        if response.data:
            df = pd.DataFrame(response.data)
            df["date"] = pd.to_datetime(df["date"])
            
            # ===== TOP STATS =====
            col1, col2, col3, col4 = st.columns(4)
            col1.metric("🦆 Total Ducks", int(df[SPECIES].sum().sum()))
            col2.metric("🎯 Total Hunts", len(df))
            col3.metric("📊 Avg per Hunt", int(df[SPECIES].sum().sum() / len(df)) if len(df) > 0 else 0)
            col4.metric("🏆 Best Hunt", int(df[SPECIES].sum(axis=1).max()))
            
            st.divider()
            
            # ===== CHARTS =====
            col1, col2 = st.columns(2)
            
            with col1:
                st.subheader("🦆 Species Breakdown")
                species_totals = df[SPECIES].sum().sort_values(ascending=False)
                species_totals.index = species_totals.index.str.replace("_", " ").str.title()
                chart_df = species_totals[species_totals > 0]
                if len(chart_df) > 0:
                    st.bar_chart(chart_df)
                else:
                    st.info("No data yet")
            
            with col2:
                st.subheader("📈 Hunts Over Time")
                hunts_per_day = df.groupby(df["date"].dt.date).size()
                st.line_chart(hunts_per_day)
            
            st.divider()
            
            # ===== LOCATION STATS =====
            st.subheader("📍 Location Performance")
            location_stats = []
            for location in df["location"].unique():
                location_df = df[df["location"] == location]
                location_stats.append({
                    "Location": location,
                    "Hunts": len(location_df),
                    "Total Ducks": int(location_df[SPECIES].sum().sum()),
                    "Avg per Hunt": int(location_df[SPECIES].sum().sum() / len(location_df)),
                    "Best": int(location_df[SPECIES].sum(axis=1).max())
                })
            
            if location_stats:
                location_df_stats = pd.DataFrame(location_stats).sort_values("Total Ducks", ascending=False)
                st.dataframe(location_df_stats, use_container_width=True, hide_index=True)
            
            st.divider()
            
            # ===== WEATHER CORRELATION =====
            st.subheader("🌡️ Weather Insights")
            col1, col2, col3 = st.columns(3)
            
            with col1:
                avg_high = df["high_temp"].mean()
                st.metric("Avg High Temp", f"{int(avg_high)}°F")
            
            with col2:
                avg_low = df["low_temp"].mean()
                st.metric("Avg Low Temp", f"{int(avg_low)}°F")
            
            with col3:
                total_rain = df["rainfall"].sum()
                st.metric("Total Rainfall", f"{total_rain:.1f} in")
            
            st.divider()
            
            # ===== TOP SPECIES MONTHLY =====
            st.subheader("📅 Top Species by Month")
            df["Month"] = df["date"].dt.strftime("%B %Y")
            
            months = sorted(df["Month"].unique())
            selected_month = st.selectbox("Select Month", months)
            
            if selected_month:
                month_df = df[df["Month"] == selected_month]
                species_monthly = month_df[SPECIES].sum().sort_values(ascending=False)
                species_monthly.index = species_monthly.index.str.replace("_", " ").str.title()
                species_monthly = species_monthly[species_monthly > 0]
                
                if len(species_monthly) > 0:
                    st.bar_chart(species_monthly)
                else:
                    st.info("No data for this month")
        
        else:
            st.info("ℹ️ No hunt data available yet. Submit some hunts to see analytics!")
    
    except Exception as e:
        logger.error(f"Analytics error: {str(e)}")
        st.error(f"❌ Error loading analytics: {str(e)}")

# ==================== FOOTER ====================
st.divider()
st.caption("🦆 DD Hunt Tracker v2.0 | Built with ❤️ for duck hunting season 2025-2026")
