#!/usr/bin/env python3
# National Weather Service (api.weather.gov) reads for a coordinate: grid point
# metadata, 12 h periods, hourly, raw grid layers, active alerts, and the Area
# Forecast Discussion. Each subcommand prints one JSON object.
#
#   python3 nws.py point    LAT LON
#   python3 nws.py forecast LAT LON
#   python3 nws.py hourly   LAT LON [--hours 72]
#   python3 nws.py grid     LAT LON
#   python3 nws.py alerts   LAT LON
#   python3 nws.py afd      CWA
#
# AFD path: GET /products/types/AFD/locations/{CWA} lists products newest first
# in '@graph' (verified for SEW); GET /products/{id} returns 'productText'.
import argparse
import re

from _common import INDENT_PARENT, emit, fetch

API = "https://api.weather.gov"
M_TO_FT = 3.28084

# -- uom -> (output unit, converter) per grid layer; layers absent here are skipped
GRID_LAYERS = {
    "snowLevel": ("ft", lambda v: v * M_TO_FT),
    "maxTemperature": ("F", lambda v: v * 9 / 5 + 32),
    "minTemperature": ("F", lambda v: v * 9 / 5 + 32),
    "windSpeed": ("mph", lambda v: v * 0.621371),
    "windGust": ("mph", lambda v: v * 0.621371),
    "quantitativePrecipitation": ("in", lambda v: v / 25.4),
    "snowfallAmount": ("in", lambda v: v / 25.4),
    "visibility": ("miles", lambda v: v / 1609.344),
    "skyCover": ("pct", lambda v: v),
    "probabilityOfPrecipitation": ("pct", lambda v: v),
}


def c_to_f(c):
    return None if c is None else round(c * 9 / 5 + 32)


def mph(s):
    """First number in a wind string like '15 mph' or '10 to 20 mph'; None when absent."""
    m = re.search(r"\d+", s or "")
    return int(m.group()) if m else None


def last(url):
    return url.rsplit("/", 1)[-1] if url else None


def point_props(lat, lon):
    return fetch(f"{API}/points/{lat},{lon}")["properties"]


def cmd_point(a):
    p = point_props(a.lat, a.lon)
    return {
        "cwa": p["cwa"],
        "office_name": fetch(f"{API}/offices/{p['cwa']}")["name"],
        "grid": {"id": p["gridId"], "x": p["gridX"], "y": p["gridY"]},
        "forecast_url": p["forecast"],
        "hourly_url": p["forecastHourly"],
        "grid_url": p["forecastGridData"],
        "forecast_zone": last(p["forecastZone"]),
        "fire_zone": last(p["fireWeatherZone"]),
        "county": last(p["county"]),
        "timezone": p["timeZone"],
        "radar": p["radarStation"],
    }


def cmd_forecast(a):
    f = fetch(point_props(a.lat, a.lon)["forecast"])["properties"]
    elev = f["elevation"]["value"]
    return {
        "issued": f["updateTime"],
        "elevation_ft": None if elev is None else round(elev * M_TO_FT),
        "periods": [
            {
                "name": p["name"],
                "start": p["startTime"],
                "end": p["endTime"],
                "is_daytime": p["isDaytime"],
                "temp_f": p["temperature"],
                "wind": f"{p['windDirection']} {p['windSpeed']}",
                "gust": p.get("windGust"),
                "precip_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
                "short": p["shortForecast"],
                "detailed": p["detailedForecast"],
            }
            for p in f["periods"]
        ],
    }


def cmd_hourly(a):
    f = fetch(point_props(a.lat, a.lon)["forecastHourly"])["properties"]
    return {
        "issued": f["updateTime"],
        "hours": [
            {
                "start": p["startTime"],
                "temp_f": p["temperature"],
                "wind_mph": mph(p["windSpeed"]),
                "gust_mph": mph(p.get("windGust")),
                "wind_dir": p["windDirection"],
                "precip_pct": (p.get("probabilityOfPrecipitation") or {}).get("value"),
                "sky": (p.get("skyCover") or {}).get("value"),
                "short": p["shortForecast"],
            }
            for p in f["periods"][: a.hours]
        ],
    }


def cmd_grid(a):
    g = fetch(point_props(a.lat, a.lon)["forecastGridData"])["properties"]
    out = {}
    for name, (unit, conv) in GRID_LAYERS.items():
        layer = g.get(name)
        if not layer or not layer.get("values"):
            continue
        rows = []
        for v in layer["values"]:
            if v["value"] is None:
                continue
            start, dur = v["validTime"].split("/")
            h = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?", dur)
            hours = int(h.group(1) or 0) * 24 + int(h.group(2) or 0)
            rows.append({"start": start, "hours": hours, "value": round(conv(v["value"]), 2), "unit": unit})
        out[name] = rows
    return out


def cmd_alerts(a):
    feats = fetch(f"{API}/alerts/active?point={a.lat},{a.lon}")["features"]
    keys = ("event", "severity", "urgency", "certainty", "headline", "onset", "ends", "expires")
    alerts = [{**{k: f["properties"].get(k) for k in keys}, "area": f["properties"]["areaDesc"], "description": f["properties"]["description"]} for f in feats]
    return {"count": len(alerts), "alerts": alerts}


def cmd_afd(a):
    latest = fetch(f"{API}/products/types/AFD/locations/{a.cwa}")["@graph"]
    newest = max(latest, key=lambda p: p["issuanceTime"])
    prod = fetch(f"{API}/products/{newest['id']}")
    return {"issued": prod["issuanceTime"], "office": a.cwa, "text": prod["productText"]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, parents=[INDENT_PARENT])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("point", cmd_point), ("forecast", cmd_forecast), ("hourly", cmd_hourly), ("grid", cmd_grid), ("alerts", cmd_alerts)):
        s = sub.add_parser(name, parents=[INDENT_PARENT])
        s.add_argument("lat", type=float)
        s.add_argument("lon", type=float)
        if name == "hourly":
            s.add_argument("--hours", type=int, default=72)
        s.set_defaults(fn=fn)
    s = sub.add_parser("afd", parents=[INDENT_PARENT])
    s.add_argument("cwa")
    s.set_defaults(fn=cmd_afd)
    a = ap.parse_args()
    emit(a.fn(a), a)


if __name__ == "__main__":
    main()
