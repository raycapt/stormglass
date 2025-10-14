import os
import folium
import streamlit as st
import pandas as pd
import stormglass_client as sgc
from stormglass_client import StormglassClient
try:
    from utils import to_knots, normalize_input_df, wind_color
except ModuleNotFoundError:
    import sys, os
    sys.path.append(os.path.dirname(__file__))
    from utils import to_knots, normalize_input_df, wind_color

st.set_page_config(page_title="Nautical Weather Map", page_icon="🌊", layout="wide")

st.title("🌊 Nautical Weather Map — Stormglass")
st.caption(
    f"Client v{getattr(sgc, '__CLIENT_VERSION__', 'unknown')} · Enter one position/time or upload many, then visualize wind, "
    "Significant wave (Hs), Wind wave, swell, and currents on a nautical chart. Data source: Stormglass (Weather/Marine/Ocean)."
)

with st.sidebar:
    st.header("Settings")
    st.markdown("""**Wind speed color thresholds (knots)**  
- `< 16` = green  
- `16–24` = orange  
- `> 24` = red""")
    st.write("---")
    st.write("Add your API key in **Secrets** as `STORMGLASS_API_KEY`.")

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
        ts = st.text_input("Timestamp (UTC) e.g., `2025-09-20 06:30` or ISO", value="2025-09-20 06:00")
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
        values = client_local.extract_values(payload, requested_iso=requested_iso)
        values = values or {}
        values["requested_iso"] = requested_iso
        values["req_lat"] = float(_lat)
        values["req_lon"] = float(_lon)
        # ---- units handling ----
        units = payload.get("_units", {})
        wind_unit = units.get("wind", "mps")
        current_unit = units.get("current", "mps")
        # Stormglass returns m/s for wind by default; convert to knots
        if "windSpeed" in values and values["windSpeed"] is not None:
            values["windSpeed_kt"] = float(to_knots(values["windSpeed"])) if wind_unit != "kn" else float(values["windSpeed"])
        # currents are m/s; convert to knots
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

    # Rename for clarity (same names as Open-Meteo variant so downstream stays identical)
    rename_map = {
        "windDirection": "windDir_deg_from",
        "waveHeight": "sigWaveHeight_m",
        "waveDirection": "sigWaveDir_deg_from",
        "windWaveHeight": "windWaveHeight_m",
        "windWaveDirection": "windWaveDir_deg_from",
        "swellDirection": "swellDir_deg_from",
        "swellHeight": "swellHeight_m",
        "currentDirection": "currentDir_deg_to",
    }
    out.rename(columns=rename_map, inplace=True)

    preferred_cols = [
        "timestamp_utc","requested_iso","iso_time","lat","lon",
        "windSpeed_kt","windDir_deg_from",
        "sigWaveHeight_m","sigWaveDir_deg_from",
        "windWaveHeight_m","windWaveDir_deg_from",
        "swellHeight_m","swellDir_deg_from",
        "currentSpeed_kt","currentDir_deg_to"
    ]
    existing = [c for c in preferred_cols if c in out.columns]
    others = [c for c in out.columns if c not in existing]
    out = out[existing + others]
    return out

def make_map(df_points: pd.DataFrame):
    if df_points.empty:
        return None
    center = [df_points["lat"].mean(), df_points["lon"].mean()]
    m = folium.Map(location=center, zoom_start=3, tiles="OpenStreetMap", control_scale=True)

    folium.TileLayer(
        tiles="https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png",
        attr="Map data: © OpenSeaMap contributors",
        name="OpenSeaMap (Nautical)",
        overlay=True,
        control=True
    ).add_to(m)

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
<b>Time (UTC):</b> {r.get('timestamp_utc') or ''}<br>
<b>Lat/Lon:</b> {r.get('lat'):.4f}, {r.get('lon'):.4f}<br>
<b>Wind:</b> {ws_txt} kt @ {r.get('windDir_deg_from','')}° (from)<br>
<b>Significant wave (Hs):</b> {r.get('sigWaveHeight_m','')} m @ {r.get('sigWaveDir_deg_from','')}° (from)<br>
<b>Wind wave:</b> {r.get('windWaveHeight_m','')} m @ {r.get('windWaveDir_deg_from','')}° (from)<br>
<b>Swell:</b> {r.get('swellHeight_m','')} m @ {r.get('swellDir_deg_from','')}° (from)<br>
<b>Current:</b> {cs_txt} kt @ {r.get('currentDir_deg_to','')}° (to)
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

    folium.LayerControl().add_to(m)
    return m

result_df = None

if do_single:
    df = pd.DataFrame([{"timestamp": ts, "lat": lat, "lon": lon}])
    try:
        df_norm = df.rename(columns={"timestamp":"timestamp","lat":"lat","lon":"lon"})
        df_norm["parsed_ts"] = pd.to_datetime(df_norm["timestamp"], utc=True, errors="coerce")
        df_norm = df_norm.dropna(subset=["parsed_ts"])
        result_df = enrich_df(df_norm)
    except Exception as e:
        st.error(f"Failed to parse/fetch: {e}")

if do_bulk and uploaded is not None:
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
    st.dataframe(result_df, use_container_width=True, hide_index=True)

    csv_bytes = result_df.to_csv(index=False).encode("utf-8")
    st.download_button("⬇️ Download CSV", data=csv_bytes, file_name="nautical_weather_results.csv", mime="text/csv")

    st.subheader("3) Map")
    m = make_map(result_df)
    if m:
        from streamlit.components.v1 import html as st_html
        st_html(m.get_root().render(), height=600, scrolling=False)
    else:
        st.info("No map to display yet.")
else:
    st.info("Enter a point or upload a file, then click **Fetch**.")
