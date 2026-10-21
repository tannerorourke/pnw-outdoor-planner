---
name: plan
description: Plan an outdoor trip in Washington, Oregon, Idaho, or the BC fringe end to end, from intake to a sourced go/no-go verdict, by resolving each day's location to its governing sources and running the pnw-outdoor-planner workflow. Use only when the user types /pnw-outdoor-planner:plan; a single weather or avalanche question goes to weather-report or avalanche-report directly.
disable-model-invocation: true
argument-hint: "[trip in a sentence, or a path to an itinerary JSON]"
---

# plan

When done, the user holds one report: a verdict on top (Go, Go with caveats, Wait with a reassess date, or No-go), the reasoning chain, a day-by-day forecast with confidence tags, one line per factor, the timing, the contingency plan, map links, the three curated interactive maps where their factor applies, and every source opened with its as-of. The verdict is a weighed judgment above six hard stops; the hard stops are applied in code and never argued down.

Three files in this folder are the contract every agent reads: `sources.yaml` (every source, what it provides, how it enters the plan, which agent owns it), `regions.yaml` (the land-unit registry with pages, permit systems, season windows, and `verify-by` dates), and `scripts/resolve.py` (the resolver that turns an itinerary into one location bundle per day). The workflow is `workflows/pnw-outdoor-planner.js` at the plugin root; the API scripts are the sibling `weather-report` and `avalanche-report` skills. `${CLAUDE_PLUGIN_ROOT}` is the plugin root in every path below.

## Critical

- Intake loops until the route, the dates, and every day's coordinates are pinned down. A GPX is an output of this skill, never an input. Never run the workflow on a trip with a day that has no trailhead coordinate.
- Run the resolver in this session before the workflow, so the user sees the bundles and every lapsed `verify-by` warning before research spends tokens. The workflow accepts the bundles as an argument.
- The report is written to disk and shown to the user; the workflow's return value is data, not the deliverable.

## Instructions

### 1. Intake

Collect, asking only for what is missing:

- Trip: name, activity (`hike`, `backpack`, `scramble`, `mountaineering`, `ski`), start and end dates, `glaciated` (true for any glacier travel), `snow` (true when the route holds snow on the dates; spring volcano season is snow), `coastal` (true for the Olympic coast), and an optional `party` string that replaces the default calibration (an experienced mountaineer and skier with glacier travel and scrambling experience).
- Per day: date, trailhead (name, lat, lon, elev_ft), camp and high point when they exist, `route_elev_band` as `[low_ft, high_ft]`, and `crossings` (name, lat, lon) for any stream the route fords. Route stats the Clock agent needs: distance and gain per day, in the day's `name` or a `stats` field.

Coordinates come from the route's own guide page, PeakBagger, or CalTopo; state the source in the itinerary so the agents can check it. Write the itinerary to `~/.cache/pnw-outdoor-planner/<trip-slug>.json`. The shape is `scripts/example-itinerary.json` (3-day Rainier) and `scripts/example-itinerary-oregon.json` (1-day South Sister).

Pass: the file parses and every day has `date` and `trailhead.lat`, `trailhead.lon`.

### 2. Resolve

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/plan/scripts/resolve.py ~/.cache/pnw-outdoor-planner/<trip-slug>.json --today YYYY-MM-DD > ~/.cache/pnw-outdoor-planner/<trip-slug>.bundles.json
```

Read the output. Tell the user, in a few lines: each day's jurisdiction, forecast office, avalanche zone (or unforecast), resolved land units, and every `warnings` line. A `lapsed verify-by` warning means a season window or permit entry in `regions.yaml` is past its re-check date; the run proceeds, and the agent reading that unit treats the window as unverified. A day with `ad_hoc: true` sits in no registry unit; the agents fall back to reverse geocoding and open search for that day's land manager.

Pass: one bundle per itinerary day, and `nws.cwa` present on every WA, OR, or ID day (a BC day carries the Environment Canada note instead). An `error` field inside a bundle names a feed that failed; the run can continue, and the agent owning that feed reports it as a gap.

### 3. Run the workflow

```
Workflow({ name: "pnw-outdoor-planner:pnw-outdoor-planner", args: { root: "${CLAUDE_PLUGIN_ROOT}", itinerary: <itinerary object>, bundles: <bundles object>, today: "YYYY-MM-DD" } })
```

`root` and `today` are required; the script has no environment and no clock. Optional: `force_snow: true` runs the Snow agent on a trip marked `snow: false`. The run spawns six research agents (Snow only when `snow` is true), re-runs Clock when Access or Ground moved the start or the distance, applies the hard stops in code, has an Opus synthesizer write the verdict and the report, and runs one completeness check with at most one revision. Expect roughly ten minutes.

Pass: the return carries `verdict`, `hard_stops`, `report`, and `agents`. A `missing_agents` entry names an agent that returned nothing; the report names it as a gap.

### 4. Deliver

Write `report` to `~/.cache/pnw-outdoor-planner/<trip-slug>.report.md` and, when the user keeps trip notes in the vault, offer to copy it there; never copy it unasked. Show the executive summary in the reply, then the path. Produce the GPX of the planned route from the itinerary's points (a track through trailhead, camp, high point per day) at `<trip-slug>.gpx` beside the report, for import into Gaia; the Gaia and CalTopo links are already in the report.

Pass: the user can open the report and the GPX, and the verdict in the reply matches the `verdict` field (the script lowers any Go the synthesizer returned above a hard-stop floor, and logs that it did).

## Maintenance

- `regions.yaml` carries a `verify_by` on every season window and permit entry. Re-verify an entry before its date and move the date into the next year; the resolver only warns.
- A source that an agent logged as `ad hoc` on a trip earns a row in `sources.yaml` after the trip, with its `agent` and how it enters the plan.
- An unclassified reading that recurs earns a factor row in `sources.yaml` under `agents.<name>.factors`.
