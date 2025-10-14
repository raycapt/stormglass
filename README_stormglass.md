# Nautical Weather Map — Stormglass

This app replaces **Open‑Meteo** with **Stormglass** while keeping the same UI, CSV output, and map.

## What changed
- New `stormglass_client.py` with a drop‑in interface (`fetch_point` / `extract_values`) matching the old client.  
- `app.py` now imports `stormglass_client` and converts wind/current from m/s → knots.
- Secrets key changed to `STORMGLASS_API_KEY`.
- Requirements are unchanged (we already use `requests` and `python-dateutil`).

## Setup
1. In Streamlit → **Settings → Secrets**, add:
   ```toml
   STORMGLASS_API_KEY = "YOUR_REAL_KEY"
   ```
   Or set an environment variable `STORMGLASS_API_KEY` on your host.

2. Deploy the two files:
   - `app.py`
   - `stormglass_client.py`

3. Restart your Streamlit app and **Clear cache**.

## Notes
- We request a 3‑hour window (`start`/`end`) around the nearest hour and pick the closest time point.  
- Stormglass returns per‑source objects (e.g., `{"noaa": 5.3, "sg": 5.4}`); the client prefers blended `sg`, then your preferred source, then any numeric fallback.
- Units: Stormglass returns SI units — wind/current in **m/s**, waves in **m** and directions in **degrees**. The app converts to **knots** for display.
