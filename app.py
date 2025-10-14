import os
import sys
from pathlib import Path as _Path
from math import radians, degrees, sin, cos, asin, atan2

import folium
import streamlit as st
import pandas as pd

import stormglass_client as sgc
from stormglass_client import StormglassClient

# Ensure we can import utils whether this file is in repo root or a subfolder
_here = _Path(__file__).resolve()
for _p in (_here.parent, _here.parent.parent):
    if _p and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from utils import to_knots, normalize_input_df, wind_color

# ---------------- Geodesy helpers for arrows ----------------
EARTH_R_M = 6371000.0

def destination_point(lat, lon, bearing_deg, distance_m):
    """Compute destination point from (lat, lon) moving distance_m along bearing_deg (0 deg = North)."""
    phi1 = radians(lat)
    lam1 = radians(lon)
    theta = radians(bearing_deg)
    delta = distance_m / EARTH_R_M

    sin_phi2 = sin(phi1) * cos(delta) + cos(phi1) * sin(delta) * cos(theta)
    phi2 = asin(sin_phi2)
    y = sin(theta) * sin(delta) * cos(phi1)
    x = cos(delta) - sin(phi1) * sin_phi2
    lam2 = lam1 + atan2(y, x)

    return degrees(phi2), (degrees(lam2) + 540) % 360 - 180  # normalize lon

def draw_vector(m, tip_lat, tip_lon, bearing_towards_tip_deg, shaft_len_m=2000, body_weight=5, color="#FF0000"):
    """Windy-like vector: thick shaft + filled triangular head at tip."""
    # Tail of shaft
    tail_bearing = (bearing_towards_tip_deg + 180.0) % 360.0
    tail_lat, tail_lon = destination_point(tip_lat, tip_lon, tail_bearing, shaft_len_m)
    # Shaft
    folium.PolyLine([[tail_lat, tail_lon], [tip_lat, tip_lon]], color=color, weight=body_weight, opacity=0.95).add_to(m)
    # Triangle head
    head_len = max(shaft_len_m * 0.22, 350)
    spread = 22.0
    left_brg = (bearing_towards_tip_deg - spread) % 360.0
    right_brg = (bearing_towards_tip_deg + spread) % 360.0
    left_lat, left_lon = destination_point(tip_lat, tip_lon, left_brg, head_len)
    right_lat, right_lon = destination_point(tip_lat, tip_lon, right_brg, head_len)
    folium.Polygon(locations=[[left_lat,left_lon],[tip_lat,tip_lon],[right_lat,right_lon]], color=color, fill=True, weight=0, fill_opacity=0.95).add_to(m)

# ---------------- Streamlit UI ----------------
st.set_page_config(page_title="Nautical Weather Map", page_icon="🌊", layout="wide")

st.title("🌊 Nautical Weather Map — Stormglass")
st.caption(
    f"Client v{getattr(sgc, '__CLIENT_VERSION__', 'unknown')} · Enter one position/time or upload many, then visualize wind, waves, swell, and currents. Data: Stormglass"
)

with st.sidebar:
    st.header("Settings")
    st.markdown("""**Wind speed color thresholds (knots)**
- < 16 = green
- 16–24 = orange
- > 24 = red""")
    st.write("---")
    st.write("Add your API key in **Secrets** as STORMGLASS_API_KEY.")
    show_current_arrows = st.checkbox("Show current arrows", value=True)
    show_wave_arrows = st.checkbox("Show significant wave arrows", value=True)

api_key = st.secrets.get("STORMGLASS_API_KEY") or os.getenv("STORMGLASS_API_KEY")
debug = st.sidebar.checkbox("Debug API params to logs", value=False)

def _safe_client(_api_key):
    try:
        return StormglassClient(api_key=_api_key or None, debug=debug)
    except TypeError:
        return StormglassClient(api_key=_api_key or None)

client = _safe_client(api_key)

st.subheader("1) Input positions & timestamps")
tab_single, tab_bulk = st.tabs(["Single point", "Bulk upload CSV/XLSX"])

with tab_single:
    c1, c2, c3 = st.columns([1,1,1])
    with c1:
        ts = st.text_input("Timestamp (UTC) e.g., 2025-09-20 06:30 or ISO", value="2025-09-20 06:00")
    with c2:
        lat = st.number_input("Latitude", value=40.0, format="%.6f")
    with c3:
        lon = st.number_input("Longitude", value=-40.0, format="%.6f")
    do_single = st.button("Fetch single point")

with tab_bulk:
    uploaded = st.file_uploader("Upload CSV/XLSX with columns: timestamp, lat, lon", type=["csv","xlsx"])
    do_bulk = st.button("Fetch uploaded points")

def _fetch_one(_lat, _lon, _ts_iso, _api_key):
    client_local = _safe_client(_api_key)
    parsed = pd.to_datetime(_ts_iso, utc=True, errors="coerce")
    if pd.isna(parsed):
        return None
    try:
        payload = client_local.fetch_point(float(_lat), float(_lon), parsed.to_pydatetime())
        requested_iso = payload.get("_requested_iso")
        values = client_local.extract_values(payload, requested_iso=requested_iso) or {}
        values["requested_iso"] = requested_iso
        values["req_lat"] = float(_lat)
        values["req_lon"] = float(_lon)
        # units
        units = payload.get("_units", {})
        wind_unit = units.get("wind", "mps")
        current_unit = units.get("current", "mps")
        # wind m/s -> knots
        if "windSpeed" in values and values["windSpeed"] is not None:
            values["windSpeed_kt"] = float(to_knots(values["windSpeed"])) if wind_unit != "kn" else float(values["windSpeed"])
        # current m/s -> knots
        if "currentSpeed" in values and values["currentSpeed"] is not None:
            values["currentSpeed_kt"] = float(to_knots(values["currentSpeed"])) if current_unit == "mps" else float(values["currentSpeed"])
        return values
    except Exception as e:
        return {"error": str(e), "requested_iso": str(_ts_iso), "req_lat": _lat, "req_lon": _lon}

def enrich_df(df_in: pd.DataFrame):
    rows = []
    for _, r in df_in.iterrows():
        res = _fetch_one(r["lat"], r["lon"], r["parsed_ts"].isoformat(), api_key)
        if res is None:
            rows.append({})
            continue
        rec = {
            "timestamp_utc": r["parsed_ts"],
            "lat": r["lat"],
            "lon": r["lon"],
        }
        rec.update(res or {})
        rows.append(rec)
    out = pd.DataFrame(rows)

    # Rename to match previous schema
    rename_map = {
        "windDirection": "windDir_deg_from",
        "waveHeight": "sigWaveHeight_m",
        "waveDirection": "sigWaveDir_deg_from",
        "windWaveHeight": "windWaveHeight_m",
        "windWaveDirection": "windWaveDir_deg_from",
        "swellDirection": "swellDir_deg_from",
        "swellHeight": "swellHeight_m",
        "currentDirection": "currentDir_deg_from",
    }
    out.rename(columns=rename_map, inplace=True)

    # Derive 'to' direction from 'from' if available
    if "currentDir_deg_from" in out.columns:
        try:
            out["currentDir_deg_to"] = (out["currentDir_deg_from"].astype(float) + 180.0) % 360.0
        except Exception:
            pass

    preferred_cols = [
        "timestamp_utc","requested_iso","iso_time","lat","lon",
        "windSpeed_kt","windDir_deg_from",
        "sigWaveHeight_m","sigWaveDir_deg_from",
        "windWaveHeight_m","windWaveDir_deg_from",
        "swellHeight_m","swellDir_deg_from",
        "currentSpeed_kt","currentDir_deg_to","currentDir_deg_from"]
    existing = [c for c in preferred_cols if c in out.columns]
    others = [c for c in out.columns if c not in existing]
    out = out[existing + others]
    return out

def make_map(df_points: pd.DataFrame, show_current_arrows=True, show_wave_arrows=True):
    if df_points.empty:
        return None
    center = [df_points["lat"].mean(), df_points["lon"].mean()]
    m = folium.Map(location=center, zoom_start=3, tiles="OpenStreetMap", control_scale=True)

    folium.TileLayer(
        tiles="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
        attr="Map data: OpenSeaMap contributors",
        name="OpenSeaMap (Nautical)",
        overlay=True,
        control=True
    ).add_to(m)

    curr_fg = folium.FeatureGroup(name='Currents', show=True)
    wave_fg = folium.FeatureGroup(name='Significant Waves', show=True)

    for _, r in df_points.iterrows():
        wind_kt = r.get("windSpeed_kt")
        color = wind_color(wind_kt if wind_kt is not None else float("nan"))
        ws = r.get("windSpeed_kt")
        cs = r.get("currentSpeed_kt")
        try:
            ws_txt = f"{float(ws):.1f}" if ws is not None and not pd.isna(ws) else ""
        except Exception:
            ws_txt = ""
        try:
            cs_txt = f"{float(cs):.1f}" if cs is not None and not pd.isna(cs) else ""
        except Exception:
            cs_txt = ""

        tt = folium.Tooltip(
            f"""
Time (UTC): {r.get('timestamp_utc') or ''}
Lat/Lon: {r.get('lat'):.4f}, {r.get('lon'):.4f}
Wind: {ws_txt} kt @ {r.get('windDir_deg_from','')} deg (from)
Significant wave (Hs): {r.get('sigWaveHeight_m','')} m @ {r.get('sigWaveDir_deg_from','')} deg (from)
Wind wave: {r.get('windWaveHeight_m','')} m @ {r.get('windWaveDir_deg_from','')} deg (from)
Swell: {r.get('swellHeight_m','')} m @ {r.get('swellDir_deg_from','')} deg (from)
Current: {cs_txt} kt @ {r.get('currentDir_deg_to','')} deg (to) / {r.get('currentDir_deg_from','')} deg (from)
""",
            sticky=True
        )

        folium.CircleMarker(
            location=[r["lat"], r["lon"]],
            radius=6,
            color=color,
            fill=True,
            fill_opacity=0.9,
            weight=1
        ).add_child(tt).add_to(m)

        # Currents
        cs_val = r.get("currentSpeed_kt")
        cd_to = r.get("currentDir_deg_to")
        if show_current_arrows and cs_val is not None and not pd.isna(cs_val) and cd_to is not None and not pd.isna(cd_to):
            if cs_val < 0.5:
                c_col = "#FFA500"  # orange
            elif cs_val > 2.0:
                c_col = "#FF0000"  # bright red
            elif cs_val > 1.0:
                c_col = "#FF7F7F"  # light red
            else:
                c_col = "#FFA500"
            c_len = 800 + float(cs_val) * 1800  # meters
            draw_vector(curr_fg, r["lat"], r["lon"], float(cd_to), shaft_len_m=c_len, body_weight=5, color=c_col)

        # Sig waves
        hs = r.get("sigWaveHeight_m")
        wd_from = r.get("sigWaveDir_deg_from")
        if show_wave_arrows and hs is not None and not pd.isna(hs) and wd_from is not None and not pd.isna(wd_from):
            wave_bearing_into_tip = float(wd_from)  # waves arriving from this bearing
            if hs < 2.0:
                w_col = "#D3D3D3"  # light grey
            elif hs > 4.0:
                w_col = "#00008B"  # dark blue
            else:
                w_col = "#696969"  # dark grey
            w_len = 700 + float(hs) * 700  # meters
            draw_vector(wave_fg, r["lat"], r["lon"], wave_bearing_into_tip, shaft_len_m=w_len, body_weight=7, color=w_col)

    curr_fg.add_to(m)
    wave_fg.add_to(m)
    folium.LayerControl().add_to(m)
    return m

result_df = None

if tab_single:
    pass  # tabs are contexts, no-ops here

# Handle actions
if 'do_single' in locals() and do_single:
    df = pd.DataFrame([{"timestamp": ts, "lat": lat, "lon": lon}])
    try:
        df_norm = df.rename(columns={"timestamp":"timestamp","lat":"lat","lon":"lon"})
        df_norm["parsed_ts"] = pd.to_datetime(df_norm["timestamp"], utc=True, errors="coerce")
        df_norm = df_norm.dropna(subset=["parsed_ts"])
        result_df = enrich_df(df_norm)
    except Exception as e:
        st.error(f"Failed to parse/fetch: {e}")

if 'do_bulk' in locals() and do_bulk and uploaded is not None:
    try:
        if uploaded.name.lower().endswith(".csv"):
            df_in = pd.read_csv(uploaded)
        else:
            df_in = pd.read_excel(uploaded)
        df_norm = normalize_input_df(df_in)
        result_df = enrich_df(df_norm)
    except Exception as e:
        st.error(f"Upload or processing error: {e}")

if isinstance(result_df, pd.DataFrame) and not result_df.empty:
    st.subheader("2) Results")
    df_display = result_df.copy()
    df_display = df_display.rename(columns={
        "currentDir_deg_to": "Current direction (going to)",
        "currentDir_deg_from": "Current direction (coming from)"
    })
    st.dataframe(df_display, use_container_width=True, hide_index=True)

    csv_bytes = result_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name="nautical_weather_results.csv", mime="text/csv")

    st.subheader("3) Map")
    m = make_map(result_df, show_current_arrows=show_current_arrows, show_wave_arrows=show_wave_arrows)
    if m:
        from streamlit.components.v1 import html as st_html
        st_html(m.get_root().render(), height=600, scrolling=False)
    else:
        st.info("No map to display yet.")
else:
    st.info("Enter a point or upload a file, then click **Fetch**.")
