---
name: weather-report
description: Fetches public weather, snow telemetry, streamflow, tide, and daylight data for a coordinate as JSON through small Python scripts (NWS forecast, hourly, grid, alerts, and Area Forecast Discussion; SNOTEL snow water equivalent and depth; USGS river discharge; NOAA tide predictions; sunrise, sunset, and moon). Use for weather, forecast, snow telemetry, SNOTEL, streamflow, river level, tide, or daylight questions and for outdoor trip planning in WA, OR, and ID. Avalanche products belong to `avalanche-report`.
---

# Weather report

Seven scripts in `${CLAUDE_PLUGIN_ROOT}/skills/weather-report/scripts/`, Python 3.13, standard library only. Each prints one JSON object to stdout. A fetch failure exits non-zero with one line on stderr (`fetch failed: URL: reason`); report it and continue with the other sources. Every script takes `--json-indent N` (default 0, compact). Run them as `python3 scripts/X.py ...`, with X one of `scripts/nws.py`, `scripts/snotel.py`, `scripts/usgs.py`, `scripts/tides.py`, `scripts/sun.py`; `scripts/_common.py` holds the shared HTTP helper and is not run directly. The only cache is the SNOTEL station list at `~/.cache/pnw-outdoor-planner/snotel-stations.json`, 30 days.

## Contract

| Command | Returns |
| --- | --- |
| `nws.py point LAT LON` | `cwa`, `office_name`, `grid {id,x,y}`, `forecast_url`, `hourly_url`, `grid_url`, `forecast_zone`, `fire_zone`, `county`, `timezone`, `radar` |
| `nws.py forecast LAT LON` | `issued`, `elevation_ft`, `periods[] {name,start,end,is_daytime,temp_f,wind,gust,precip_pct,short,detailed}` |
| `nws.py hourly LAT LON [--hours 72]` | `issued`, `hours[] {start,temp_f,wind_mph,gust_mph,wind_dir,precip_pct,sky,short}` |
| `nws.py grid LAT LON` | per layer `[{start,hours,value,unit}]` for snowLevel (ft), max/minTemperature (F), windSpeed/windGust (mph), quantitativePrecipitation/snowfallAmount (in), visibility (miles), skyCover/probabilityOfPrecipitation (pct); empty layers omitted |
| `nws.py alerts LAT LON` | `count`, `alerts[] {event,severity,urgency,certainty,headline,onset,ends,expires,area,description}` |
| `nws.py afd CWA` | `issued`, `office`, `text` |
| `snotel.py nearest LAT LON [--elev FT] [--n 5]` | `stations[] {triplet,name,elevation_ft,lat,lon,distance_km,elev_diff_ft}`; `elev_diff_ft` appears with `--elev` |
| `snotel.py data TRIPLET [--days 3]` | `station`, `daily[] {date,swe_in,depth_in,temp_f,precip_in}`, `change {swe_24h_in,swe_72h_in,depth_24h_in,depth_72h_in}` |
| `usgs.py nearest LAT LON [--km 25] [--n 5]` | `sites[] {site,name,lat,lon,distance_km}` |
| `usgs.py flow SITE [--days 3]` | `site`, `name`, `series[] {time,cfs,stage_ft}` (hourly), `latest`, `trend_24h`, `diurnal` |
| `tides.py nearest LAT LON [--n 3]` | `stations[] {id,name,lat,lon,distance_km}` |
| `tides.py predict STATION DATE [--days 1]` | `station`, `days[] {date, events[] {time,type H/L,height_ft}}` |
| `sun.py LAT LON DATE [--tz America/Los_Angeles]` | `date`, `civil_dawn`, `sunrise`, `sunset`, `civil_dusk`, `daylight_hours`, `moon_phase`, `moon_illumination_pct` |

Typical order: `nws.py point` once per coordinate for `cwa` and `timezone`, then the rest. `sun.py` needs no network.

## Reading the output

- **Forecast vs hourly vs grid.** `forecast` gives 12 h periods with prose and is the right grain for a day-scale read. `hourly` gives timing within a day; gusts are mostly null and `sky` is null, so take sky cover from `grid`. `grid` is the raw numeric forecast and the only source of `snowLevel`, `snowfallAmount`, and `visibility`. Grid values are on the grid cell's elevation (see `elevation_ft` in `forecast`), not the summit.
- **AFD.** The Area Forecast Discussion is the only place the forecaster states confidence, model disagreement, and which scenario is favored. Read it before trusting a precipitation or timing number from the other products. `issued` is UTC.
- **Alerts.** `count` 0 means no active watch or warning at the point, which says nothing about the forecast.
- **SNOTEL.** `change` fields are loading proxies: SWE gain over 24 h and 72 h approximates new load on the snowpack, and depth gain approximates new snow. Both come from the first and last available days, so a day with a missing value shortens the window. Pick a station near the trip elevation with `--elev`; the score is distance in km plus 5 km per 1000 ft of elevation difference. Today's values are usually absent, so the last day is yesterday.
- **USGS.** `diurnal` compares afternoon (12 to 18 local) to morning (04 to 10) flow on each full day. A diurnal rise means the river peaks in the evening and is lowest in the morning, so stream crossings should happen in the morning. `trend_24h` is rising or falling beyond 5% against 24 h earlier. Glacial and snowmelt rivers can rise on clear warm days with no rain.
- **Tides.** `events` fix headland and beach-walk windows: passable ground below a headland usually needs a falling or low tide, so choose crossing times from the `L` events and their heights, and check them against the daylight window from `sun.py`. Heights are feet above MLLW in local standard or daylight time.
- **Sun.** `civil_dawn` and `civil_dusk` bound usable light without a headlamp. Times are local to `--tz`. Moon phase is a mean-cycle approximation, accurate to about a day.

## Method notes

- The AFD path: `GET https://api.weather.gov/products/types/AFD/locations/{CWA}` lists products in `@graph`; the script takes the newest `issuanceTime`, then reads `productText` from `GET https://api.weather.gov/products/{id}`. Verified for SEW on 2026-10-20.
- AWDB parameters used: `stations` takes `stationTriplets` (wildcards `*:WA:SNTL,*:OR:SNTL,*:ID:SNTL`) and `activeOnly`; `data` takes `stationTriplets`, `elements`, `duration`, `beginDate`, `endDate`. `networkCds` and `stateCds` are absent from the service's OpenAPI spec and are ignored by the server.
- The AWDB `data` endpoint intermittently stalls beyond the 20 s timeout. `fetch` retries once on a timeout, then exits non-zero.

## Example output (Paradise, 46.7860 -121.7350, 2026-10-20)

`nws.py point`:

```json
{"cwa": "SEW", "office_name": "Seattle/Tacoma, WA", "grid": {"id": "SEW", "x": 136, "y": 26}, "forecast_zone": "WAZ304", "fire_zone": "WAZ659", "county": "WAC053", "timezone": "America/Los_Angeles", "radar": "KRTX"}
```

`nws.py forecast`, one period:

```json
{"issued": "2026-10-20T18:26:39+00:00", "elevation_ft": 5151, "periods": [{"name": "Wednesday", "is_daytime": true, "temp_f": 57, "wind": "W 0 to 5 mph", "gust": null, "precip_pct": 2, "short": "Sunny"}]}
```

`nws.py grid`, first value of two layers:

```json
{"snowLevel": [{"start": "2026-10-20T12:00:00+00:00", "hours": 3, "value": 7297.0, "unit": "ft"}], "windGust": [{"start": "2026-10-20T12:00:00+00:00", "hours": 1, "value": 10.36, "unit": "mph"}]}
```

`nws.py afd SEW`, text cut:

```json
{"issued": "2026-10-21T03:51:00+00:00", "office": "SEW", "text": "\n000\nFXUS66 KSEW 210351\nAFDSEW\n\nArea Forecast Discussion\n..."}
```

`snotel.py data 679:WA:SNTL`, days cut to one:

```json
{"station": {"triplet": "679:WA:SNTL", "name": "Paradise", "elevation_ft": 5150}, "daily": [{"date": "2026-10-19", "swe_in": 0.0, "depth_in": 0, "temp_f": 41.5, "precip_in": 0.8}], "change": {"swe_24h_in": 0.0, "swe_72h_in": 0.0, "depth_24h_in": 0, "depth_72h_in": 0}}
```

`usgs.py flow 12082500`, series cut to one point:

```json
{"site": "12082500", "name": "NISQUALLY RIVER NEAR NATIONAL, WA", "series": [{"time": "2026-10-17T23:00:00.000-07:00", "cfs": 233.0, "stage_ft": 4.29}], "latest": {"time": "2026-10-20T22:00:00.000-07:00", "cfs": 261.0, "stage_ft": 4.36}, "trend_24h": "rising", "diurnal": "no afternoon rise over 3 days (mean ratio 0.88): no melt cycle"}
```

`tides.py predict 9442396 2026-10-24`:

```json
{"station": "9442396", "days": [{"date": "2026-10-24", "events": [{"time": "05:27", "type": "L", "height_ft": 1.651}, {"time": "11:38", "type": "H", "height_ft": 9.093}, {"time": "18:08", "type": "L", "height_ft": 0.145}]}]}
```

`sun.py 46.786 -121.735 2026-10-24`:

```json
{"date": "2026-10-24", "civil_dawn": "07:06", "sunrise": "07:37", "sunset": "18:04", "civil_dusk": "18:35", "daylight_hours": 10.45, "moon_phase": "full moon", "moon_illumination_pct": 97}
```
