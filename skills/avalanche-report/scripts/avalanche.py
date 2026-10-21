#!/usr/bin/env python3
# Resolves a coordinate to its avalanche forecast zone (US: avalanche.org, Canada:
# Avalanche Canada) and returns the current product in one normalized JSON shape.
# Map layers are cached 24 h under ~/.cache/pnw-outdoor-planner/; products are live.
# Fetch failure exits 1 with one stderr line.
#   avalanche.py zone LAT LON
#   avalanche.py forecast LAT LON
#   avalanche.py levels

import html
import json
import math
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

UA = "pnw-outdoor-planner (github.com/tannerorourke)"
TIMEOUT = 20
CACHE = Path.home() / ".cache" / "pnw-outdoor-planner"
TTL = 24 * 3600
US_LAYER = "https://api.avalanche.org/v2/public/products/map-layer"
US_PRODUCT = "https://api.avalanche.org/v2/public/product?type=forecast&center_id={}&zone_id={}"
CA_AREAS = "https://api.avalanche.ca/forecasts/en/areas"
CA_PRODUCTS = "https://api.avalanche.ca/forecasts/en/products"

LEVELS = {"low": 1, "moderate": 2, "considerable": 3, "high": 4, "extreme": 5}
LABELS = {v: k.capitalize() for k, v in LEVELS.items()}
COMPASS = {"north", "northeast", "east", "southeast", "south", "southwest", "west", "northwest"}
COMPASS_ABBR = {"n": "north", "ne": "northeast", "e": "east", "se": "southeast",
                "s": "south", "sw": "southwest", "w": "west", "nw": "northwest"}
BAND_NAMES = {"alp": "alpine", "tln": "treeline", "btl": "below treeline",
              "upper": "alpine", "middle": "treeline", "lower": "below treeline"}


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read().decode("utf-8")
    except Exception as e:
        sys.exit(f"avalanche.py: fetch failed for {url}: {e}")


def cached_json(name: str, url: str):
    path = CACHE / name
    if path.exists() and time.time() - path.stat().st_mtime < TTL:
        return json.loads(path.read_text())
    body = fetch(url)
    data = json.loads(body)
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return data


def text(s) -> str | None:
    if not s:
        return None
    s = re.sub(r"</(p|li|h\d)>|<br\s*/?>", "\n", s)
    s = html.unescape(re.sub(r"<[^>]+>", "", s))
    return re.sub(r"\n\s*\n+", "\n", s).strip() or None


def ring_has(ring, x, y) -> bool:
    inside = False
    j = len(ring) - 1
    for i, (xi, yi, *_) in enumerate(ring):
        xj, yj = ring[j][0], ring[j][1]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def polygon_has(rings, x, y) -> bool:
    return ring_has(rings[0], x, y) and not any(ring_has(h, x, y) for h in rings[1:])


def geometry_has(geom, x, y) -> bool:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    return any(polygon_has(p, x, y) for p in polys)


def bbox_center(geom) -> tuple[float, float]:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    pts = [pt for p in polys for pt in p[0]]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


def km(lat1, lon1, lat2, lon2) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(a))


def ca_products() -> dict:
    """Area id -> current product; a live fetch, the title names the area."""
    return {p["area"]["id"]: p for p in json.loads(fetch(CA_PRODUCTS))}


def ca_today_rating(report) -> dict | None:
    """Rating entry for today: the latest dated entry not after now, else the first."""
    days = report.get("dangerRatings") or []
    now = datetime.now(timezone.utc)
    past = [d for d in days if datetime.fromisoformat(d["date"]["value"].replace("Z", "+00:00")) <= now]
    return (past[-1] if past else days[0]) if days else None


def ca_band(entry) -> tuple[int | None, str]:
    rid = entry["rating"]["value"]
    return LEVELS.get(rid), entry["rating"]["display"]


def ca_zone(feat, products) -> dict:
    prod = products.get(feat["id"])
    zone = {"country": "CA", "center_id": "AVCAN", "center": "Avalanche Canada",
            "zone_id": feat["id"], "zone": feat["id"], "off_season": prod is None,
            "danger_level": None, "danger": None, "url": None}
    if prod:
        r = prod["report"]
        zone["off_season"] = all(ca_band(b)[0] is None for d in r.get("dangerRatings") or [] for b in d["ratings"].values())
        # -- out of season the regions merge into one area titled with every region name
        title = r["title"] if len(r["title"]) <= 80 else "Avalanche Canada merged early-season area"
        zone.update(center=r["forecaster"], zone=title, url=prod["url"])
        today = ca_today_rating(r)
        if today:
            bands = [ca_band(b) for b in today["ratings"].values()]
            rated = [b for b in bands if b[0]]
            if rated:
                top = max(rated)
                zone.update(danger_level=top[0], danger=top[1])
            else:
                zone["danger"] = bands[0][1]
    return zone


def us_zone(feat) -> dict:
    p = feat["properties"]
    level = p["danger_level"]
    return {"country": "US", "center_id": p["center_id"], "center": p["center"],
            "zone_id": str(feat["id"]), "zone": p["name"], "off_season": bool(p["off_season"]),
            "danger_level": level if level > 0 else None,
            "danger": p["danger"].title() if level > 0 else None, "url": p["link"]}


def locate(lat, lon) -> dict:
    """Zone dict for the point, or the unforecast dict with the nearest centroid."""
    for f in cached_json("avalanche-org-map-layer.json", US_LAYER)["features"]:
        if geometry_has(f["geometry"], lon, lat):
            return us_zone(f)
    areas = cached_json("avalanche-ca-areas.json", CA_AREAS)["features"]
    products = ca_products()
    for f in areas:
        if geometry_has(f["geometry"], lon, lat):
            return ca_zone(f, products)
    cands = [(km(lat, lon, c[1], c[0]), f, "US") for f in cached_json("avalanche-org-map-layer.json", US_LAYER)["features"]
             for c in [bbox_center(f["geometry"])]]
    cands += [(km(lat, lon, f["properties"]["centroid"][1], f["properties"]["centroid"][0]), f, "CA") for f in areas]
    dist, feat, country = min(cands, key=lambda c: c[0])
    z = us_zone(feat) if country == "US" else ca_zone(feat, products)
    return {"unforecast": True, "nearest": {"zone": z["zone"], "center_id": z["center_id"],
                                            "country": country, "distance_km": round(dist, 1)}}


def parse_location(items) -> tuple[list, list]:
    """Split avalanche.org problem locations ('northeast upper', 'n', 'alp') into aspects and bands."""
    aspects, bands = [], []
    for item in items or []:
        for tok in re.split(r"[\s,]+", str(item).lower()):
            tok = COMPASS_ABBR.get(tok, tok)
            if tok in COMPASS and tok not in aspects:
                aspects.append(tok)
            elif tok in BAND_NAMES and BAND_NAMES[tok] not in bands:
                bands.append(BAND_NAMES[tok])
    return aspects, bands


def us_product(zone) -> dict:
    body = fetch(US_PRODUCT.format(zone["center_id"], zone["zone_id"])).strip()
    try:
        raw = json.loads(body) if body else {}
    except json.JSONDecodeError:
        raw = {}
    if raw.get("product_type") != "forecast":
        stmt = text(raw.get("hazard_discussion") or raw.get("bottom_line"))
        return {"zone": {**zone, "off_season": True}, "product": None, "off_season": True,
                "note": "No daily forecast is published; the center is off season or in an early-season statement.",
                "statement": stmt[:1500] if stmt else None}
    pub = datetime.fromisoformat(raw["published_time"]).date()
    offsets = {"current": 0, "tomorrow": 1, "day2": 2}
    danger = []
    for d in raw.get("danger") or []:
        off = offsets.get(d["valid_day"])
        lv = {"below_treeline": d.get("lower"), "near_treeline": d.get("middle"), "above_treeline": d.get("upper")}
        danger.append({"date": str(pub + timedelta(days=off)) if off is not None else d["valid_day"], **lv,
                       "labels": {k: LABELS.get(v) for k, v in lv.items()}})
    problems = []
    for p in raw.get("forecast_avalanche_problems") or []:
        aspects, bands = parse_location(p.get("location"))
        problems.append({"name": p.get("name"), "aspects": aspects, "elevations": bands,
                         "likelihood": p.get("likelihood"), "size": p.get("size"),
                         "discussion": text(p.get("discussion"))})
    return {"zone": zone, "product": {
        "issued": raw["published_time"], "expires": raw["expires_time"],
        "bottom_line": text(raw.get("bottom_line")), "discussion": text(raw.get("hazard_discussion")),
        "confidence": None, "danger": danger, "problems": problems}}


def ca_product(zone) -> dict:
    prod = ca_products().get(zone["zone_id"])
    r = prod["report"]
    summaries = {s["type"]["value"]: text(s["content"]) for s in r.get("summaries") or []}
    danger = []
    for d in r.get("dangerRatings") or []:
        bands = {k: ca_band(d["ratings"][k]) for k in ("btl", "tln", "alp")}
        danger.append({"date": d["date"]["value"][:10],
                       "below_treeline": bands["btl"][0], "near_treeline": bands["tln"][0],
                       "above_treeline": bands["alp"][0],
                       "labels": {"below_treeline": bands["btl"][1], "near_treeline": bands["tln"][1],
                                  "above_treeline": bands["alp"][1]}})
    problems = []
    for p in r.get("problems") or []:
        d = p.get("data") or {}
        size = d.get("expectedSize")
        problems.append({
            "name": p["type"]["display"],
            "aspects": [COMPASS_ABBR.get(a["value"], a["value"]) for a in d.get("aspects") or []],
            "elevations": [e["display"] for e in d.get("elevations") or []],
            "likelihood": (d.get("likelihood") or {}).get("display"),
            "size": [size["min"], size["max"]] if size else None,
            "discussion": text(p.get("comment"))})
    conf = (r.get("confidence") or {}).get("rating") or {}
    discussion = "\n".join(filter(None, [text(r.get("highlights")), summaries.get("avalanche-summary"),
                                         summaries.get("snowpack-summary"), summaries.get("weather-summary")]))
    if all(d["above_treeline"] is None for d in danger):
        return {"zone": {**zone, "off_season": True}, "product": None, "off_season": True,
                "note": "Avalanche Canada publishes no numeric ratings yet (early season or closed).",
                "statement": discussion}
    return out


def forecast(lat, lon) -> dict:
    zone = locate(lat, lon)
    if zone.get("unforecast"):
        return {"zone": zone, "product": None, "unforecast": True,
                "note": f"No avalanche forecast covers this point; nearest is {zone['nearest']['zone']} "
                        f"({zone['nearest']['distance_km']} km by centroid)."}
    return us_product(zone) if zone["country"] == "US" else ca_product(zone)


def main():
    cmd, *args = sys.argv[1:]
    if cmd == "levels":
        out = {"scale": {str(v): k.capitalize() for k, v in LEVELS.items()}, "hard_stop_at": 4}
    else:
        lat, lon = map(float, args)
        out = locate(lat, lon) if cmd == "zone" else forecast(lat, lon)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
