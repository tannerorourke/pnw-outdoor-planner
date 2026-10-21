---
name: avalanche-report
description: Resolves a coordinate to its avalanche forecast zone and returns the current danger rating, avalanche problems, and forecaster discussion for Washington, Oregon, Idaho, and British Columbia (US via avalanche.org, Canada via Avalanche Canada). Use when a request mentions avalanche, snowpack, danger rating, or snow trip planning such as ski touring, snowshoeing, or a spring volcano climb. Weather and telemetry belong to `weather-report`.
---

# Avalanche report

One script resolves a point to its zone and normalizes the current product. Run it, then apply the interpretation rules below; the script never judges a route.

## Run

```
python3 ${CLAUDE_PLUGIN_ROOT}/skills/avalanche-report/scripts/avalanche.py <command>
```

| Command | Returns |
| --- | --- |
| `zone LAT LON` | `country` (`US`/`CA`), `center_id`, `center`, `zone_id`, `zone`, `off_season`, `danger_level` (1-5 or null), `danger`, `url`. No polygon contains the point: `{"unforecast": true, "nearest": {zone, center_id, country, distance_km}}`. |
| `forecast LAT LON` | `{zone, product}`. `product` holds `issued`, `expires`, `bottom_line`, `discussion`, `confidence` (CA only, else null), `danger[]` (`date`, `below_treeline`, `near_treeline`, `above_treeline`, `labels`), `problems[]` (`name`, `aspects`, `elevations`, `likelihood`, `size`, `discussion`). |
| `levels` | The scale 1 Low, 2 Moderate, 3 Considerable, 4 High, 5 Extreme, plus `hard_stop_at: 4`. |

- Off season, early season, or unforecast: `product` is null, with `off_season: true` or `unforecast: true` and a `note`. An early-season statement from the center, when one exists, is in `statement`.
- Fetch failure: exit 1 and one stderr line. Report the failure; never substitute a remembered rating.
- Map layers cache 24 hours in `~/.cache/pnw-outdoor-planner/`. Products are always live.
- `zone` reads the cached layer, so its `off_season` can lag the center. `forecast` is authoritative: a product that is not a daily forecast sets `off_season: true`.
- A null level in `danger[]` means no numeric rating (early season or unrated), never Low.

## Coverage

| Region | Source | Notes |
| --- | --- | --- |
| WA Cascades and Olympics, Mt Hood | `NWAC` | Closes in spring; reopens around December. |
| Central Oregon (Bachelor, Sisters) | `COAA` | Center id is `COAA`. |
| Southern Oregon | `SOAIX` | Observation exchange, publishes no forecast. Treat as unforecast. |
| Sawtooths, Smokys, Wood River | `SNFAC` | Center id is `SNFAC`. |
| North Idaho | `IPAC` | Panhandle ranges. |
| British Columbia | `AVCAN` | Out of season the regions merge into one area, so `zone` names the merged area and the product cannot distinguish Sea to Sky from its neighbors. |

## Interpretation rules

1. The rating is an input. Lay each problem's aspects and elevations over the route's aspects, the slope angles at each crux, and the day it crosses them.
2. A rating of High (4) or Extreme (5) in any band the route touches, on the day it touches it, is a hard stop. Say so first and offer no workaround.
3. Considerable (3) is a terrain-selection problem. An experienced party with a plan for the listed aspects and elevations can proceed; an inexperienced party cannot.
4. Read `danger[]` per band and per date. The headline `danger_level` is the maximum across bands, so it overstates a route that stays below treeline.
5. Recent observed activity on the route's aspect (human-triggered slides, collapsing, shooting cracks) outweighs the published rating. A Moderate forecast with a fresh slide on the route's aspect is treated as Considerable.
6. `unforecast: true` or a null product: write "no forecast available" in the plan and drop the trip's confidence one step. Reason from `weather-report` data, telemetry, and any published observations; the nearest zone in `nearest` is context, never a stand-in.
7. Spring volcano season (Rainier, Baker, Hood, Adams, St. Helens after NWAC closes) is the common unforecast case. Wet-loose and glide hazards peak after warm nights with no refreeze, so the freeze level and overnight low decide the start time.
8. An expired product (`expires` before the trip day) is stale. Use it for the problem list and say the rating is not current.
9. Early-season statements with no rating still mean avalanches occur wherever snow covers terrain steep enough to slide. Treat as unforecast.

## Example

Illustrative shape of an in-season `forecast` (values invented):

```json
{
  "zone": {"country": "US", "center_id": "NWAC", "zone": "West Slopes South",
           "off_season": false, "danger_level": 3, "danger": "Considerable"},
  "product": {
    "issued": "2027-01-14T14:00:00+00:00", "expires": "2027-01-15T14:00:00+00:00",
    "bottom_line": "Wind slabs on leeward slopes above treeline...",
    "confidence": null,
    "danger": [{"date": "2027-01-14", "below_treeline": 2, "near_treeline": 3,
                "above_treeline": 3, "labels": {"above_treeline": "Considerable"}}],
    "problems": [{"name": "Wind Slab", "aspects": ["north", "northeast", "east"],
                  "elevations": ["alpine", "treeline"], "likelihood": "likely",
                  "size": ["1", "2"], "discussion": "..."}]
  }
}
```

Reading it: a route on a south-facing ridge above treeline meets a Considerable rating but no listed problem aspect, so the wind slab is not on the route; the same route's north-facing descent is.
