#!/usr/bin/env python3
# NOAA CO-OPS tide reads: nearest prediction stations to a point and
# high/low predictions (MLLW, feet, local standard/daylight time).
#
#   python3 tides.py nearest LAT LON [--n 3]
#   python3 tides.py predict STATION DATE [--days 1]    # DATE is YYYY-MM-DD
import argparse
from datetime import date, timedelta

from _common import INDENT_PARENT, emit, fetch, haversine_km

MD = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json?type=tidepredictions"
DATA = "https://api.tidesandcurrents.noaa.gov/api/prod/datagetter"


def cmd_nearest(a):
    rows = [
        {"id": s["id"], "name": s["name"], "lat": round(s["lat"], 4), "lon": round(s["lng"], 4), "distance_km": round(haversine_km(a.lat, a.lon, s["lat"], s["lng"]), 1)}
        for s in fetch(MD)["stations"]
    ]
    return {"stations": sorted(rows, key=lambda r: r["distance_km"])[: a.n]}


def cmd_predict(a):
    start = date.fromisoformat(a.date)
    end = start + timedelta(days=a.days - 1)
    q = f"product=predictions&datum=MLLW&units=english&time_zone=lst_ldt&format=json&interval=hilo&station={a.station}&begin_date={start:%Y%m%d}&end_date={end:%Y%m%d}"
    j = fetch(f"{DATA}?{q}")
    if "predictions" not in j:
        raise SystemExit(f"fetch failed: station {a.station}: {j.get('error', {}).get('message', j)}")
    days = {}
    for p in j["predictions"]:
        d, t = p["t"].split(" ")
        days.setdefault(d, []).append({"time": t, "type": p["type"], "height_ft": float(p["v"])})
    return {"station": a.station, "days": [{"date": d, "events": e} for d, e in days.items()]}


def main():
    ap = argparse.ArgumentParser(description=__doc__, parents=[INDENT_PARENT])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("nearest", parents=[INDENT_PARENT])
    s.add_argument("lat", type=float)
    s.add_argument("lon", type=float)
    s.add_argument("--n", type=int, default=3)
    s.set_defaults(fn=cmd_nearest)
    s = sub.add_parser("predict", parents=[INDENT_PARENT])
    s.add_argument("station")
    s.add_argument("date")
    s.add_argument("--days", type=int, default=1)
    s.set_defaults(fn=cmd_predict)
    a = ap.parse_args()
    emit(a.fn(a), a)


if __name__ == "__main__":
    main()
