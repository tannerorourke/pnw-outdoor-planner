#!/usr/bin/env python3
# USGS Water Services reads: active discharge sites near a point and recent
# instantaneous flow with a 24 h trend and a melt-cycle (diurnal) read.
#
#   python3 usgs.py nearest LAT LON [--km 25] [--n 5]
#   python3 usgs.py flow SITE [--days 3]
import argparse
import math
from datetime import datetime
from statistics import mean

from _common import INDENT_PARENT, emit, fetch, haversine_km

IV = "https://waterservices.usgs.gov/nwis/iv/"
SITE = "https://waterservices.usgs.gov/nwis/site/"
TREND_BAND = 0.05
DIURNAL_BAND = 0.03


def cmd_nearest(a):
    dlat = a.km / 111.0
    dlon = a.km / (111.0 * math.cos(math.radians(a.lat)))
    bbox = f"{a.lon - dlon:.4f},{a.lat - dlat:.4f},{a.lon + dlon:.4f},{a.lat + dlat:.4f}"
    rdb = fetch(f"{SITE}?format=rdb&bBox={bbox}&parameterCd=00060&siteStatus=active", raw=True)
    rows = [l.split("\t") for l in rdb.splitlines() if l and not l.startswith("#")][2:]
    sites = []
    for r in rows:
        lat, lon = float(r[4]), float(r[5])
        sites.append({"site": r[1], "name": r[2], "lat": lat, "lon": lon, "distance_km": round(haversine_km(a.lat, a.lon, lat, lon), 1)})
    return {"sites": sorted(sites, key=lambda s: s["distance_km"])[: a.n]}


def diurnal(series):
    """Compare mean afternoon (12-18 local) to mean morning (04-10 local) flow over whole days."""
    by_day = {}
    for p in series:
        t = datetime.fromisoformat(p["time"])
        by_day.setdefault(t.date(), {"am": [], "pm": []})
        if 4 <= t.hour < 10:
            by_day[t.date()]["am"].append(p["cfs"])
        elif 12 <= t.hour < 18:
            by_day[t.date()]["pm"].append(p["cfs"])
    ratios = [mean(d["pm"]) / mean(d["am"]) for d in by_day.values() if d["am"] and d["pm"] and mean(d["am"]) > 0]
    if not ratios:
        return "insufficient data for a diurnal read"
    up = sum(r > 1 + DIURNAL_BAND for r in ratios)
    r = mean(ratios)
    if up == len(ratios):
        return f"afternoon flow exceeds morning on {up} of {len(ratios)} days (mean {100 * (r - 1):.0f}% higher): melt cycle"
    if up == 0:
        return f"no afternoon rise over {len(ratios)} days (mean ratio {r:.2f}): no melt cycle"
    return f"afternoon exceeds morning on {up} of {len(ratios)} days: mixed"


def trend(series):
    day_ago = datetime.fromisoformat(series[-1]["time"]).timestamp() - 86400
    ref = min(series, key=lambda p: abs(datetime.fromisoformat(p["time"]).timestamp() - day_ago))
    change = (series[-1]["cfs"] - ref["cfs"]) / max(ref["cfs"], 1)
    return "rising" if change > TREND_BAND else "falling" if change < -TREND_BAND else "steady"


def cmd_flow(a):
    j = fetch(f"{IV}?format=json&sites={a.site}&parameterCd=00060,00065&period=P{a.days}D")
    ts = j["value"]["timeSeries"]
    if not ts:
        raise SystemExit(f"no data for site {a.site}")
    name = ts[0]["sourceInfo"]["siteName"]
    by_time = {}
    for s in ts:
        field = "cfs" if s["variable"]["variableCode"][0]["value"] == "00060" else "stage_ft"
        for v in s["values"][0]["value"]:
            by_time.setdefault(v["dateTime"], {})[field] = float(v["value"]) if float(v["value"]) > -999 else None
    full = [{"time": t, "cfs": d.get("cfs"), "stage_ft": d.get("stage_ft")} for t, d in sorted(by_time.items())]
    flow = [p for p in full if p["cfs"] is not None]
    hourly = [p for p in full if p["time"][14:16] == "00"] or full
    return {"site": a.site, "name": name, "series": hourly, "latest": full[-1], "trend_24h": trend(flow), "diurnal": diurnal(flow)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, parents=[INDENT_PARENT])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("nearest", parents=[INDENT_PARENT])
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.add_argument("--km", type=float, default=25)
    s.add_argument("--n", type=int, default=5)
    s.set_defaults(fn=cmd_nearest)
    s = sub.add_parser("flow", parents=[INDENT_PARENT])
    s.add_argument("site")
    s.add_argument("--days", type=int, default=3)
    s.set_defaults(fn=cmd_flow)
    a = ap.parse_args()
    emit(a.fn(a), a)


if __name__ == "__main__":
    main()
