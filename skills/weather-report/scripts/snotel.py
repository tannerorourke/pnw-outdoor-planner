#!/usr/bin/env python3
# NRCS SNOTEL reads through the AWDB REST service: nearest stations to a point
# and recent daily snow/temperature/precipitation for one station.
#
#   python3 snotel.py nearest LAT LON [--elev FT] [--n 5]
#   python3 snotel.py data TRIPLET [--days 3]
#
# AWDB parameters used (OpenAPI at /awdbRestApi/v3/api-docs):
#   /services/v1/stations: stationTriplets (wildcard '*:WA:SNTL'), activeOnly
#   /services/v1/data: stationTriplets, elements, duration, beginDate, endDate
# 'networkCds' and 'stateCds' are not in the spec and the server ignores them,
# so the state filter is the wildcard triplet.
# Station list cache: ~/.cache/pnw-outdoor-planner/snotel-stations.json, 30 days.
import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

from _common import INDENT_PARENT, emit, fetch, haversine_km

BASE = "https://wcc.sc.egov.usda.gov/awdbRestApi/services/v1"
CACHE = Path.home() / ".cache/pnw-outdoor-planner/snotel-stations.json"
CACHE_TTL_S = 30 * 86400
ELEV_KM_PER_KFT = 5


def station_record(s):
    return {"triplet": s["stationTriplet"], "name": s["name"], "elevation_ft": round(s["elevation"]), "lat": s["latitude"], "lon": s["longitude"]}


def stations():
    if CACHE.exists() and time.time() - CACHE.stat().st_mtime < CACHE_TTL_S:
        return json.loads(CACHE.read_text())
    raw = fetch(f"{BASE}/stations?stationTriplets=*:WA:SNTL,*:OR:SNTL,*:ID:SNTL&activeOnly=true")
    out = [station_record(s) for s in raw]
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(out))
    return out


def cmd_nearest(a):
    rows = []
    for s in stations():
        d = haversine_km(a.lat, a.lon, s["lat"], s["lon"])
        row = {**s, "distance_km": round(d, 1)}
        score = d
        if a.elev is not None:
            row["elev_diff_ft"] = s["elevation_ft"] - round(a.elev)
            score += abs(row["elev_diff_ft"]) / 1000 * ELEV_KM_PER_KFT
        rows.append((score, row))
    return {"stations": [r for _, r in sorted(rows, key=lambda t: t[0])[: a.n]]}


def delta(vals, days_back):
    """Last value minus the value days_back earlier, or the first available when the window is shorter."""
    if len(vals) < 2:
        return None
    ref = vals[max(0, len(vals) - 1 - days_back)]
    return None if ref is None or vals[-1] is None else round(vals[-1] - ref, 1)


def cmd_data(a):
    end = date.today()
    begin = end - timedelta(days=a.days)
    q = f"stationTriplets={a.triplet}&elements=WTEQ,SNWD,TOBS,PREC&duration=DAILY&beginDate={begin}&endDate={end}"
    raw = fetch(f"{BASE}/data?{q}")
    by_day = {}
    key = {"WTEQ": "swe_in", "SNWD": "depth_in", "TOBS": "temp_f", "PREC": "precip_in"}
    for el in raw[0]["data"] if raw else []:
        for v in el["values"]:
            by_day.setdefault(v["date"], {})[key[el["stationElement"]["elementCode"]]] = v.get("value")
    daily = [{"date": d, **{k: by_day[d].get(k) for k in key.values()}} for d in sorted(by_day)]
    meta = next((s for s in stations() if s["triplet"] == a.triplet), {"triplet": a.triplet})
    swe, depth = [r["swe_in"] for r in daily], [r["depth_in"] for r in daily]
    change = {"swe_24h_in": delta(swe, 1), "swe_72h_in": delta(swe, 3), "depth_24h_in": delta(depth, 1), "depth_72h_in": delta(depth, 3)}
    return {"station": meta, "daily": daily, "change": change}


def main():
    ap = argparse.ArgumentParser(description=__doc__, parents=[INDENT_PARENT])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("nearest", parents=[INDENT_PARENT])
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.add_argument("--elev", type=float)
    s.add_argument("--n", type=int, default=5)
    s.set_defaults(fn=cmd_nearest)
    s = sub.add_parser("data", parents=[INDENT_PARENT])
    s.add_argument("triplet")
    s.add_argument("--days", type=int, default=3)
    s.set_defaults(fn=cmd_data)
    a = ap.parse_args()
    emit(a.fn(a), a)


if __name__ == "__main__":
    main()
