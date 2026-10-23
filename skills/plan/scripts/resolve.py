#!/usr/bin/env python3
"""Resolve an itinerary into one daily location bundle per day.

A bundle names the regional sources that govern the day: jurisdiction, forecast
office, avalanche zone, land units from the registry, nearest instruments,
season switches. Agents read the bundle; they never pick regional sources.
Fetches are deduplicated across days by (script, args).

  python3 resolve.py ITINERARY.json [--today YYYY-MM-DD] [--regions PATH] [--sources PATH]
  cat itinerary.json | python3 resolve.py -
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import date
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import yaml

SKILL = Path(__file__).resolve().parent.parent
WX = SKILL.parent / "weather-report/scripts"
AVY = SKILL.parent / "avalanche-report/scripts/avalanche.py"
CODES = {"WA", "OR", "ID", "BC"}

AUTHORITY = {
    "WA": ("WSDOT", ["WTA"], "WA DNR with USFS and NPS", "Northwest Forest Pass or America the Beautiful on federal land; Discover Pass on state land"),
    "OR": ("ODOT", ["Oregon Hikers"], "Oregon Dept of Forestry with USFS and BLM", "Northwest Forest Pass or America the Beautiful on federal land; state park day-use permit"),
    "ID": ("Idaho 511", ["AllTrails"], "Idaho Dept of Lands with USFS", "America the Beautiful on federal land; Idaho Park Passport on state land"),
    "BC": ("DriveBC", ["AllTrails"], "BC Wildfire Service", "BC Parks day-use pass where required"),
}
TZ = {"WA": "America/Los_Angeles", "OR": "America/Los_Angeles", "ID": "America/Boise", "BC": "America/Vancouver"}
# -- (start, end) as MM-DD, inclusive; start > end wraps the new year
SWITCHES = {
    "nwac_forecasting": ("11-01", "04-30"),
    "sno_park": ("11-01", "04-30"),
    "st_helens_quota": ("04-01", "10-31"),
    "central_cascades_permits": ("06-15", "10-15"),
    "fire_season": ("06-01", "10-31"),
}
WEBCAM_KM, WEBCAM_N = 60, 3


def jurisdiction(lat, lon):
    if lat > 49.0:
        return "BC"
    if lon > -117.04:
        return "ID"
    return ("WA" if lat >= (46.25 if lon < -122.0 else 46.0) else "OR")


def in_window(d: date, start: str, end: str) -> bool:
    md = d.strftime("%m-%d")
    return start <= md <= end if start <= end else md >= start or md <= end


_cache = {}


def run(script, *args):
    key = (str(script), args)
    if key not in _cache:
        try:
            p = subprocess.run([sys.executable, str(script), *map(str, args)], capture_output=True, text=True, timeout=90)
            if p.returncode:
                lines = p.stderr.strip().splitlines()
                _cache[key] = {"error": lines[-1] if lines else f"exit {p.returncode}"}
            else:
                _cache[key] = json.loads(p.stdout)
        except (subprocess.TimeoutExpired, json.JSONDecodeError) as e:
            _cache[key] = {"error": f"{type(e).__name__}: {e}"}
    return _cache[key]


def nws_point(lat, lon):
    return run(WX / "nws.py", "point", lat, lon)


def snotel_nearest(lat, lon, elev):
    return run(WX / "snotel.py", "nearest", lat, lon, "--elev", round(elev), "--n", 3)


def usgs_nearest(lat, lon):
    return run(WX / "usgs.py", "nearest", lat, lon, "--km", 25, "--n", 3)


def tides_nearest(lat, lon):
    return run(WX / "tides.py", "nearest", lat, lon, "--n", 1)


def sun_times(lat, lon, day, tz):
    return run(WX / "sun.py", lat, lon, day, "--tz", tz)


def avalanche_zone(lat, lon):
    return run(AVY, "zone", lat, lon)


def load_yaml(path):
    return yaml.safe_load(Path(path).read_text()) or {}


def load_units(path, warnings):
    paths = [Path(path)] if path else [SKILL / "regions.yaml"]
    if not paths[0].exists() and not path:
        paths = [SKILL / "regions-wa-nps.yaml", SKILL / "regions-or-id-bc.yaml"]
    units = []
    for p in paths:
        if p.exists():
            units += load_yaml(p).get("units") or []
        else:
            warnings.append(f"registry not found: {p}")
    return units


def area(u):
    w, s, e, n = u["bbox"]
    return (e - w) * (n - s)


def contains(u, pt):
    w, s, e, n = u["bbox"]
    return w <= pt["lon"] <= e and s <= pt["lat"] <= n


def km(a, b):
    h = sin(radians(b["lat"] - a["lat"]) / 2) ** 2 + \
        cos(radians(a["lat"])) * cos(radians(b["lat"])) * sin(radians(b["lon"] - a["lon"]) / 2) ** 2
    return 12742 * asin(sqrt(h))


def nearest_webcams(units, hits, th):
    # -- placed cams rank by distance across the whole registry, since a forest-sized unit holds cams
    #    150 km apart; an unplaced cam rides on its unit containing the day
    placed = {}
    for w in (w for u in units for w in u.get("webcams") or [] if "lat" in w):
        placed.setdefault(w["url"], w)
    ranked = sorted((km(th, w), url, w) for url, w in placed.items())
    cams = [{**w, "km": round(d, 1)} for d, _, w in ranked if d <= WEBCAM_KM][:WEBCAM_N]
    unplaced = [w for u in hits for w in u.get("webcams") or [] if "lat" not in w]
    return cams, unplaced


def unit_entry(u, d: date, today: str, lapsed: list):
    def live(w):
        return bool(w) and in_window(d, str(w["start"]), str(w["end"]))

    permits = [p for p in u.get("permits") or [] if not p.get("window") or live(p["window"])]
    seasons = [{**s, "live": live(s.get("window"))} for s in u.get("seasons") or []]
    mine = [f"lapsed verify-by: {u['id']} {x['name']} ({x['verify_by']})"
            for x in (u.get("permits") or []) + (u.get("seasons") or [])
            if x.get("verify_by") and str(x["verify_by"]) < today]
    lapsed += mine
    return {"id": u["id"], "name": u["name"], "kind": u.get("kind"), "manager": u.get("manager"),
            "pages": u.get("pages"), "map_url": u.get("map_url"), "map_center_template": u.get("map_center_template"), "permits_in_window": permits,
            "seasons": seasons, "notes": u.get("notes"), "lapsed_verify_by": mine}


def source_applies(src, juris):
    region = src.get("region") or []
    region = [region] if isinstance(region, str) else region
    return any(r in juris or r not in CODES for r in region)


def resolve_day(day, trip, units, sources, today):
    d = date.fromisoformat(day["date"])
    pts = {k: day[k] for k in ("trailhead", "camp", "high_point") if day.get(k)}
    th, hp = pts["trailhead"], pts.get("high_point") or pts["trailhead"]
    juris = list(dict.fromkeys(jurisdiction(p["lat"], p["lon"]) for p in pts.values()))
    primary = juris[0]
    road, community, fire, passes = AUTHORITY[primary]
    community = community + (["Turns All Year"] if trip.get("activity") == "ski" else [])

    if primary == "BC":
        nws = {"bc": True, "note": "Environment Canada and SpotWx replace the NWS pair"}
    else:
        nws = nws_point(hp["lat"], hp["lon"])

    if not trip.get("snow"):
        avy = {"skipped": "snow false"}
    else:
        zones = [avalanche_zone(p["lat"], p["lon"]) for p in ({id(x): x for x in (th, hp)}.values())]
        avy = zones[0] if len(zones) == 1 or zones[0] == zones[1] else {"zones": zones}

    lapsed = []
    hits = [u for u in sorted(units, key=area) if any(contains(u, p) for p in pts.values())]
    entries = [unit_entry(u, d, today, lapsed) for u in hits]

    band = day.get("route_elev_band") or [th.get("elev_ft"), hp.get("elev_ft")]
    band = [b for b in band if b is not None]
    inst = {"snotel": snotel_nearest(hp["lat"], hp["lon"], sum(band) / len(band)) if band else {"error": "no elevation"}}
    inst["snotel"] = inst["snotel"].get("stations", inst["snotel"])
    if day.get("crossings"):
        inst["usgs"] = [{"crossing": c["name"], **usgs_nearest(c["lat"], c["lon"])} for c in day["crossings"]]
    if trip.get("coastal"):
        t = tides_nearest(th["lat"], th["lon"])
        inst["tides"] = t.get("stations", t)
    cams, unplaced = nearest_webcams(units, hits, th)
    inst["webcams"] = cams + unplaced
    if not cams:
        inst["webcams_note"] = f"no registry webcam within {WEBCAM_KM} km of the trailhead; search for one near the route or lean on satellite"

    tz = nws.get("timezone") or TZ[primary]
    bundle = {
        "date": day["date"], "points": pts, "jurisdiction": primary, "jurisdictions": juris,
        "road_authority": road, "trip_report_community": community, "fire_agency": fire, "pass_rules": passes,
        "nws": nws, "avalanche": avy, "units": entries,
    }
    if not entries:
        bundle["ad_hoc"] = {"jurisdiction": primary, "note": "no registry unit contains the day's points; reverse geocode for the manager and run an open search"}
    bundle["instruments"] = inst
    bundle["sun"] = sun_times(th["lat"], th["lon"], day["date"], tz)
    bundle["season_switches"] = {k: in_window(d, *w) for k, w in SWITCHES.items()} | {
        "unit_seasons": [{"unit": e["id"], **s} for e in entries for s in e["seasons"]]}
    bundle["sources"] = [s["id"] for s in sources if source_applies(s, juris)]
    return bundle, lapsed


def centered(entry, lat, lon, zoom):
    # -- a 'map_center_template' names where a map takes its center; without one the curated URL is handed as is
    tpl = entry.get("map_center_template")
    return tpl.format(map_url=entry["map_url"], lat=lat, lon=lon, zoom=zoom) if tpl else entry["map_url"]


def build_maps(trip, day1, sources, bundles):
    th = day1["trailhead"]
    lat, lon = th["lat"], th["lon"]
    by_id = {s["id"]: s for s in sources}
    snotel = (by_id.get("snotel") or {}).get("map_url")
    if snotel:
        for key, val in (("lat", lat), ("lon", lon), ("zoom", 10)):
            snotel = re.sub(rf"(?<=[#&]){key}=[^&]*", f"{key}={val}", snotel)
    duty_src = next((s for s in sources if re.search(r"watch.?duty", s["id"]) and s.get("map_url")), None)
    duty = centered(duty_src, lat, lon, 9) if duty_src else None
    maps = {"gaia": f"https://www.gaiagps.com/map/?loc=13/{lon}/{lat}"}
    if trip.get("snow"):
        # -- 'o=sa' is the slope-angle overlay; this CalTopo fragment format is unverified
        maps["caltopo"] = f"https://caltopo.com/map.html#ll={lat},{lon}&z=13&b=mbt&o=sa&n=0.25"
    maps["nwcc_snotel"] = snotel
    maps["watchduty"] = duty
    seen = {}
    for b in bundles:
        for u in b["units"]:
            if u.get("map_url"):
                seen[u["id"]] = centered(u, lat, lon, 10)
    maps["unit_maps"] = [{"unit": k, "url": v} for k, v in seen.items()]
    return maps


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("itinerary", help="JSON path, or '-' for stdin")
    ap.add_argument("--today", default=date.today().isoformat())
    ap.add_argument("--regions")
    ap.add_argument("--sources", default=str(SKILL / "sources.yaml"))
    a = ap.parse_args()

    it = json.load(sys.stdin if a.itinerary == "-" else open(a.itinerary))
    warnings = []
    units = load_units(a.regions, warnings)
    sp = Path(a.sources)
    if sp.exists():
        sources = load_yaml(sp).get("sources") or []
    else:
        sources = []
        warnings.append(f"sources not found: {sp}")

    bundles, lapsed = [], []
    for day in it["days"]:
        b, lp = resolve_day(day, it["trip"], units, sources, a.today)
        bundles.append(b)
        lapsed += lp
    warnings += list(dict.fromkeys(lapsed))
    out = {"trip": it["trip"], "warnings": warnings, "days": bundles,
           "maps": build_maps(it["trip"], it["days"][0], sources, bundles)}
    json.dump(out, sys.stdout, indent=2, default=str)
    print()


if __name__ == "__main__":
    main()
