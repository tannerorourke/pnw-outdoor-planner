# pnw-outdoor-planner

A Claude Code plugin that researches an outdoor trip in Washington, Oregon, Idaho, or the British Columbia fringe and returns one verdict: Go, Go with caveats, Wait with a reassess date, or No-go, with every reading that fed it cited to its source and its as-of time.

The night-before problem it solves is assembling facts spread across five to ten sites (the NWS, an avalanche center, trip reports, a forest road page, recreation.gov, a guide blog) and weighing them for one route on one set of dates. Six research agents do the assembly in parallel, one per question a planner asks. A short list of hard stops is applied in code. Everything past the hard stops is a weighed judgment written by a synthesizer that shows its chain, so the verdict can be checked rather than trusted.

## Install

```
claude plugin marketplace add tannerorourke/pnw-outdoor-planner
claude plugin install pnw-outdoor-planner@pnw-outdoor-planner
```

Python 3.11 or later with PyYAML is needed for the resolver; the fetch scripts use the standard library only.

## Use

```
/pnw-outdoor-planner:plan Mt. Rainier via the Disappointment Cleaver, June 20 to 22
```

The `plan` skill runs intake until the route, the dates, and each day's coordinates are pinned down, resolves every day to its governing sources (forecast office, avalanche zone, land units with their pages and permit systems, nearest instruments, season switches), runs the `pnw-outdoor-planner` workflow, and writes the report and a GPX of the planned route. Expect about ten minutes for the workflow.

The two fetch skills stand on their own:

```
/pnw-outdoor-planner:weather-report      NWS forecast, hourly, grid, alerts, and office discussion; SNOTEL; USGS flow; NOAA tides; sun and moon
/pnw-outdoor-planner:avalanche-report    avalanche.org and Avalanche Canada zone lookup and the current product in one shape
```

## What is in the box

| Path | Holds |
| --- | --- |
| `workflows/pnw-outdoor-planner.js` | The workflow: Resolve, Research (Access, Sky, Ground, Snow, Clock, Contingency), Re-time, Synthesize, Check |
| `skills/plan/` | Intake and delivery, `sources.yaml` (every source, what it provides, how its reading enters the plan, which agent owns it), `regions.yaml` (land units with pages, permits, season windows, and `verify_by` dates), `scripts/resolve.py` |
| `skills/weather-report/scripts/` | `nws.py`, `snotel.py`, `usgs.py`, `tides.py`, `sun.py`, each a CLI that prints JSON |
| `skills/avalanche-report/scripts/` | `avalanche.py`: point to zone by polygon, product normalized across the two countries, the off-season and unforecast cases declared |

## Hard stops

Any one of these sets the verdict without weighing: avalanche danger High or Extreme in a band the route touches on the day it touches it; a warning-level NWS product, forecast lightning, forecast whiteout, or gusts over 35 mph at the exposed elevation on the push day; a road closed with no documented alternative, or a closure order over the route; a required permit with no availability and no walk-up path; a glaciated route with no condition report inside 14 days and no webcam or satellite confirmation; a fire perimeter or evacuation level over the route or its exit, or a volcano alert above Normal.

The default party calibration is an experienced mountaineer and skier with glacier travel and scrambling experience, so a Considerable avalanche day is a terrain-selection problem rather than an automatic stop. Pass a `party` string in the itinerary to replace it.

## Maintenance

Season windows and permit entries in `regions.yaml` carry a `verify_by` date; the resolver warns past it and never refuses. A source an agent logged as ad hoc on a trip earns a row in `sources.yaml` after the trip.

Scripts cache station lists and avalanche polygons under `~/.cache/pnw-outdoor-planner/`.

## License

MIT. Keep the copyright line if you redistribute it.
