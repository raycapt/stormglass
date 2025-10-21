import math
import requests
from datetime import datetime, timezone, timedelta
from dateutil import parser as dtparser

__CLIENT_VERSION__ = "1.0"  # Stormglass adapter

BASE_URL = "https://api.stormglass.io/v2/weather/point"

class StormglassClient:
    """
    Thin client that mirrors the interface of OpenMeteoClient.fetch_point()/extract_values()
    so the rest of the app can stay unchanged.
    """
    def __init__(self, api_key: str = None, timeout: int = 20, debug: bool = False, preferred_source: str = "sg"):
        self.api_key = api_key
        self.timeout = timeout
        self.debug = debug
        # 'sg' is Stormglass' blended source; fallback chain can include 'noaa','metno','icon','gfs', etc.
        self.preferred_source = preferred_source or "sg"

    def nearest_hour(self, dtobj: datetime):
        if dtobj.tzinfo is None:
            dtobj = dtobj.replace(tzinfo=timezone.utc)
        else:
            dtobj = dtobj.astimezone(timezone.utc)
        return dtobj.replace(minute=0, second=0, microsecond=0)

    def _get(self, params: dict):
        headers = {}
        if self.api_key:
            headers["Authorization"] = self.api_key
        if self.debug:
            # Build a preview URL for debugging (without leaking the key)
            from urllib.parse import urlencode
            preview = f"{BASE_URL}?{urlencode(params)}"
            print("DEBUG GET", preview)
        r = requests.get(BASE_URL, headers=headers, params=params, timeout=self.timeout)
        r.raise_for_status()
        return r.json()

    def _pick_index(self, hours: list, requested_iso: str):
        if not hours:
            return 0
        try:
            t_req = dtparser.isoparse(requested_iso) if requested_iso else None
        except Exception:
            t_req = None
        if t_req is None:
            return 0
        if t_req.tzinfo is None:
            t_req = t_req.replace(tzinfo=timezone.utc)

        best_i, best_dt = 0, None
        for i, h in enumerate(hours):
            t = h.get("time")
            if not t:
                continue
            try:
                dt = dtparser.isoparse(t)
            except Exception:
                continue
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            if best_dt is None or abs((dt - t_req).total_seconds()) < abs((best_dt - t_req).total_seconds()):
                best_dt, best_i = dt, i
        return best_i

    def _get_value(self, hour_obj: dict, key: str):
        """
        Stormglass returns objects like: key: { noaa: 3.4, sg: 3.5, ... }
        We try 'sg' first, then preferred_source, then any available numeric value.
        """
        val = hour_obj.get(key)
        if isinstance(val, dict):
            # Try blended 'sg'
            if "sg" in val and self._is_number(val["sg"]):
                return float(val["sg"])
            # Try configured preferred source
            if self.preferred_source in val and self._is_number(val[self.preferred_source]):
                return float(val[self.preferred_source])
            # Fallback: first numeric
            for v in val.values():
                if self._is_number(v):
                    return float(v)
            return None
        # Some accounts can enable "source=sg" which returns a plain number
        return float(val) if self._is_number(val) else None

    def _is_number(self, x):
        try:
            float(x)
            return True
        except Exception:
            return False

    def fetch_point(self, lat: float, lon: float, dtobj: datetime):
        target = self.nearest_hour(dtobj)

        # Stormglass wants start/end as ISO8601 with timezone; request a 3-hour window to ensure coverage, pick nearest
        start = (target - timedelta(hours=1)).isoformat()
        end   = (target + timedelta(hours=1)).isoformat()

        params = {
            "lat": float(lat),
            "lng": float(lon),
            # Request just what we need for this app
            "params": ",".join([
                "windSpeed","windDirection",
                "waveHeight","waveDirection",
                "swellHeight","swellDirection",
                "windWaveHeight","windWaveDirection",
                "currentSpeed","currentDirection"
            , "waterTemperature"]),
            # If your subscription supports single-source responses, you can add: "source": "sg"
            "start": start,
            "end": end,
        }

        try:
            data = self._get(params)
        except Exception as e:
            if self.debug:
                print("WARN stormglass fetch:", e)
            data = {}

        return {
            "_requested_iso": target.isoformat(),
            "_raw": data,
            "_units": {"wind": "mps", "current": "mps"},  # Stormglass uses SI by default
        }

    def extract_values(self, payload: dict, requested_iso: str = None):
        requested_iso = requested_iso or payload.get("_requested_iso")
        out = {"iso_time": None}

        raw = payload.get("_raw", {}) or {}
        hours = raw.get("hours", []) if isinstance(raw, dict) else []
        if not hours:
            # Some responses return "data" instead of "hours" (legacy); try both
            hours = raw.get("data", []) if isinstance(raw, dict) else []

        if hours:
            i = self._pick_index(hours, requested_iso)
            h = hours[i] if i < len(hours) else {}
            out["iso_time"] = h.get("time")

            # Map the variables to our app schema
            for k in [
                "windSpeed","windDirection",
                "waveHeight","waveDirection",
                "swellHeight","swellDirection",
                "windWaveHeight","windWaveDirection",
                "currentSpeed","currentDirection", "waterTemperature"
            ]:
                out[k] = self._get_value(h, k)
        else:
            for k i"windSpeed","windDirection",
                "waveHeight","waveDirection",
                "swellHeight","swellDirection",
                "windWaveHeight","windWaveDirection",
                "currentSpeed","currentDirection", "waterTemperature"","currentDirection"
            ]:
                out[k] = None

        return out
