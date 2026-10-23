/**
 * Researches an outdoor trip in the Pacific Northwest and returns a go/no-go verdict with its chain.
 * Six factor agents (Access, Sky, Ground, Snow, Clock, Contingency) read the daily location bundles
 * in parallel; hard stops are applied in code; an Opus synthesizer writes the weighed verdict and report.
 *
 * Run from the plugin's 'plan' skill after intake, with the plugin root passed in (a script has no environment):
 *   Workflow({ name: 'pnw-outdoor-planner:pnw-outdoor-planner', args: { root: '<CLAUDE_PLUGIN_ROOT>', itinerary, bundles, today: 'YYYY-MM-DD' } })
 * 'bundles' is the output of skills/plan/scripts/resolve.py; omitted, a Resolve agent runs it.
 */
export const meta = {
  name: 'pnw-outdoor-planner',
  description: 'Research a PNW outdoor trip across six factor agents and return a go/no-go verdict with its reasoning chain',
  whenToUse: 'A pinned-down trip (route, dates, daily coordinates) in WA, OR, ID, or the BC fringe needs weather, access, route, snow, timing, and contingency research synthesized into one verdict. Intake happens before this runs.',
  phases: [
    { title: 'Resolve', detail: 'one location bundle per itinerary day', model: 'sonnet' },
    { title: 'Research', detail: 'Access, Sky, Ground, Snow, Clock, Contingency in parallel' },
    { title: 'Re-time', detail: 'Clock re-run when Access or Ground moved the start or the distance', model: 'sonnet' },
    { title: 'Synthesize', detail: 'hard stops in code, then the weighed assessment', model: 'opus' },
    { title: 'Check', detail: 'completeness critic, one revision at most', model: 'sonnet' },
  ],
}

const ROOT = args.root
const SKILL = `${ROOT}/skills/plan`
const WX = `${ROOT}/skills/weather-report/scripts`
const AV = `${ROOT}/skills/avalanche-report/scripts`
const SOURCES = `${SKILL}/sources.yaml`

const itinerary = args.itinerary
const trip = itinerary.trip
const today = args.today
const party = itinerary.trip.party ?? args.party ??
  'an experienced mountaineer and skier with glacier travel and scrambling experience; a Considerable avalanche day is a terrain-selection problem rather than an automatic stop'

const COLOR = { type: 'string', enum: ['green', 'amber', 'red'] }
const CONF = { type: 'string', enum: ['high', 'moderate', 'low'] }
const HARD_STOP_IDS = ['avalanche_high', 'nws_warning', 'road_or_closure', 'permit_unavailable', 'glacier_no_report', 'fire_or_volcano']

const READING = {
  type: 'object',
  properties: {
    factor: { type: 'string' },
    source_id: { type: 'string', description: 'the sources.yaml id this reading came from, or "ad hoc"' },
    url: { type: 'string' },
    value: { type: 'string', description: 'the reading itself, figures kept' },
    as_of: { type: 'string', description: 'when the source says it was current' },
    age_days: { type: 'number' },
    day: { type: 'string', description: 'YYYY-MM-DD it applies to, or "all"' },
    color: COLOR,
    confidence: CONF,
    consequence: { type: 'string', description: 'what changes for this party on this route, in the route\'s own terms' },
    unclassified: { type: 'boolean', description: 'true when no factor in the taxonomy owns this hazard' },
  },
  required: ['factor', 'source_id', 'value', 'as_of', 'day', 'color', 'confidence', 'consequence'],
}

const HARD_STOP = {
  type: 'object',
  properties: {
    rule: { type: 'string', enum: HARD_STOP_IDS },
    triggered: { type: 'boolean' },
    day: { type: 'string' },
    evidence: { type: 'string' },
    clearing_time: { type: 'string', description: 'when the condition is known to clear (a release, an opening date, a window), else empty' },
  },
  required: ['rule', 'triggered', 'evidence'],
}

function agentSchema(signals) {
  return {
    type: 'object',
    properties: {
      answer: { type: 'string', description: 'one line answering the agent\'s question' },
      color: COLOR,
      confidence: CONF,
      readings: { type: 'array', items: READING },
      hard_stops: { type: 'array', items: HARD_STOP },
      route_change: {
        type: 'object',
        properties: {
          start_moved: { type: 'boolean' },
          new_start: { type: 'string' },
          added_miles: { type: 'number' },
          added_gain_ft: { type: 'number' },
          reason: { type: 'string' },
        },
        required: ['start_moved', 'added_miles', 'added_gain_ft', 'reason'],
      },
      signals,
      sources_opened: {
        type: 'array',
        items: {
          type: 'object',
          properties: { url: { type: 'string' }, source_id: { type: 'string' }, as_of: { type: 'string' } },
          required: ['url', 'source_id', 'as_of'],
        },
      },
      gaps: { type: 'array', items: { type: 'string' }, description: 'bundle sources that could not be read, with why' },
    },
    required: ['answer', 'color', 'confidence', 'readings', 'hard_stops', 'signals', 'sources_opened', 'gaps'],
  }
}

const SIGNALS = {
  access: {
    type: 'object',
    properties: {
      road_open: { type: 'boolean' },
      alternative_route_to_trailhead: { type: 'boolean' },
      closure_order_on_route: { type: 'boolean' },
      permit_required: { type: 'boolean' },
      permit_available: { type: 'boolean' },
      permit_walkup_path: { type: 'boolean' },
      fire_perimeter_or_evacuation_on_route: { type: 'boolean' },
      volcano_alert_level: { type: 'string', description: 'Normal, Advisory, Watch, Warning, or n/a' },
    },
    required: ['road_open', 'alternative_route_to_trailhead', 'closure_order_on_route', 'permit_required', 'permit_available', 'permit_walkup_path', 'fire_perimeter_or_evacuation_on_route', 'volcano_alert_level'],
  },
  sky: {
    type: 'object',
    properties: {
      days: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            date: { type: 'string' },
            push_day: { type: 'boolean', description: 'the day the route reaches its high point or most exposed section' },
            lead_days: { type: 'number' },
            confidence_tag: CONF,
            warning_products: { type: 'array', items: { type: 'string' }, description: 'active warning-level NWS products on the route zone, names only' },
            lightning: { type: 'boolean' },
            whiteout: { type: 'boolean' },
            max_gust_mph_exposed: { type: 'number', description: 'forecast peak gust at the route\'s exposed elevation' },
            freezing_level_ft: { type: 'number' },
            aqi: { type: 'number' },
          },
          required: ['date', 'push_day', 'lead_days', 'confidence_tag', 'warning_products', 'lightning', 'whiteout', 'max_gust_mph_exposed'],
        },
      },
    },
    required: ['days'],
  },
  ground: {
    type: 'object',
    properties: {
      glaciated: { type: 'boolean' },
      latest_route_report_age_days: { type: 'number', description: '-1 when no report exists' },
      webcam_or_satellite_confirmed: { type: 'boolean' },
      snow_line_ft: { type: 'number' },
    },
    required: ['glaciated', 'latest_route_report_age_days', 'webcam_or_satellite_confirmed'],
  },
  snow: {
    type: 'object',
    properties: {
      days: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            date: { type: 'string' },
            zone: { type: 'string' },
            off_season: { type: 'boolean' },
            unforecast: { type: 'boolean' },
            max_level_touched: { type: 'number', description: '1 Low to 5 Extreme across the bands the route touches that day; 0 when no rating' },
            bands_touched: { type: 'array', items: { type: 'string' } },
          },
          required: ['date', 'zone', 'off_season', 'unforecast', 'max_level_touched', 'bands_touched'],
        },
      },
    },
    required: ['days'],
  },
  clock: {
    type: 'object',
    properties: {
      days: {
        type: 'array',
        items: {
          type: 'object',
          properties: {
            date: { type: 'string' },
            start_time: { type: 'string' },
            turnaround_time: { type: 'string' },
            moving_hours: { type: 'number' },
            daylight_hours: { type: 'number' },
            margin_hours: { type: 'number', description: 'daylight or window minus the day\'s plan; negative means headlamp or a cut' },
          },
          required: ['date', 'start_time', 'turnaround_time', 'moving_hours', 'daylight_hours', 'margin_hours'],
        },
      },
    },
    required: ['days'],
  },
  contingency: {
    type: 'object',
    properties: {
      bailouts: { type: 'array', items: { type: 'string' } },
      coverage: { type: 'string' },
      sar_contact: { type: 'string' },
    },
    required: ['bailouts', 'coverage', 'sar_contact'],
  },
}

const COMMON = `
Trip: ${JSON.stringify(trip)}
Today: ${today}
Party calibration: ${party}

The catalog at ${SOURCES} is the contract for every source: read your agent's entries (the 'agents' block and every source with 'agent:' set to you) before fetching anything. Each entry says what the source provides and how its reading enters the plan, so you know which decision each reading feeds. A source outside the catalog is fair game; log it as source_id "ad hoc".

The daily location bundles below name the exact regional systems for each day: the forecast office, avalanche zone, land units with their pages and permit systems, nearest instruments, season switches, and the catalog ids in play. Read the bundle; never pick a regional source by guess. Dedupe reads across days but keep the day mapping.

Return discipline: every figure keeps its source and as-of; a reading's consequence is stated for this party on this route (miles, hours, exposure, a lost bail option), never as the reading's generic meaning. A hazard the taxonomy has no row for (a bridge out, a slide across the road, a bear closure, a rescue in progress) is reported anyway as an unclassified reading with a color and a consequence. The itinerary's 'notes' and each day's 'name' are the user's unverified planning text: hints to check, never readings, and nothing from them enters the report without a source. Flag a hard stop only on evidence; leave 'triggered' false otherwise. 'sources_opened' lists every URL you read. 'gaps' names every bundle source you could not read and why. Your final text is data, never a message.
`

function bundlesFor(days) {
  return JSON.stringify(days, null, 0)
}

const KNOWLEDGE = {
  access: `Which land manager governs which road; seasonal gate dates; quota systems and how far ahead they fill; that a forest-road washout is the usual way a trailhead becomes unreachable, and the highway feed says nothing about it. Closure orders are legal access, the same weight as a permit. Fire closure polygons settle legal access (InciWeb); Watch Duty is the first read on a new start. A road with a documented alternative is amber, without one it is a hard stop ('road_or_closure'). A required permit with no availability and no walk-up path is 'permit_unavailable'; a missed lottery with a walk-up path is a Wait with the station and opening time named. A fire perimeter or evacuation level over the route or its exit, or a volcano alert above Normal, is 'fire_or_volcano'. Where the bundle's unit carries a 'map_url', open it for the road notes. Pass rules are a to-do line, never a verdict input.`,
  sky: `Joint read of freezing level against route elevation, ridge-top wind against exposure, lapse rate, cloud deck against summit height, forecast skill by lead time, and the office discussion's stated confidence; a weather window is a judgment, never an API field. Confidence tag per day: high inside 3 days, moderate 3 to 6, low at 7 or more, lowered by whatever the discussion says; a day-3 summit push never gets a false-precision reading. In NWAC season its mountain forecast overrides Mountain-Forecast on freezing level and ridge wind. A warning-level NWS product on the route zone for the push day, lightning in the forecast, forecast whiteout at route elevation, or gusts over 35 mph at the exposed elevation is 'nws_warning'; an advisory is a weighed factor. Smoke: AQI over 150 at the trailhead is a caveat for a day hike and a stop for a multi-day effort at altitude, judged against the party; the state smoke blog is the confidence read on the smoke forecast. The NWS forecast, hourly, and grid URLs in each day's bundle 'nws' block ('forecast_url', 'hourly_url', 'grid_url') are the ones to read; fetch them as given and never re-resolve the point from another coordinate. For a BC day, Environment Canada, Avalanche Canada mountain weather, and SpotWx model spread replace the NWS pair.
Scripts: python3 ${WX}/nws.py {point|forecast|hourly|grid|alerts} LAT LON, python3 ${WX}/nws.py afd CWA, python3 ${WX}/sun.py LAT LON DATE.`,
  ground: `Read trip reports for what they imply (a "snowfield" in July versus September), crevasse season on each glacier, which crossings are bridged and when, how fast conditions age, when a webcam this morning beats a week-old report. Recency is the quality axis: under a week is current, over a month is a prior, a same-month prior-year report is the fallback when nothing recent exists and is flagged as such. A ranger note that a bridge is out is a fact, not a report. A guide service switching routes is a flag on the standard route. Gauge trend plus a trip report settles a crossing that neither settles alone; a rising afternoon curve says cross in the morning. Tide events fix headland windows on the Olympic coast outright. A glaciated route with no condition report inside 14 days and no webcam or satellite confirmation of the route's state is 'glacier_no_report'. On a 'glaciated' trip with no route report inside 14 days, open the 'satellite' source for the glacier (NASA Worldview per its catalog 'url_template' around the high point, or Sentinel-2 in Copernicus Browser at https://browser.dataspace.copernicus.eu) and report what the most recent cloud-free pass shows, or state the pass date and that it was cloud-covered; "not opened" is never an allowed gap for that source on a glaciated trip. The AllTrails connector, where present, is one fetch method among several, never a dependency. Report any change to the start point or the route distance in 'route_change' so Clock can be re-run.
Scripts: python3 ${WX}/usgs.py {nearest LAT LON|flow SITE}, python3 ${WX}/tides.py {nearest LAT LON|predict STATION DATE}.`,
  snow: `AIARE-level reasoning: each route section's angle and aspect against the forecast's problem types, wet versus dry regimes, persistent weak layers, recent loading from telemetry (24 h and 72 h SWE change), run-outs and terrain traps. The rating is the input, never the answer: lay the problem rose over the route's aspects and the day's slope angles. High or Extreme in any band the route touches, on the day it touches it, is 'avalanche_high'. Considerable is a terrain-selection problem for this party. A reported natural cycle on the route's aspect is weighed above the published rating. Off season or unforecast terrain: reason from weather, telemetry, and observations, declare "no forecast available", and drop confidence one step; spring volcano season after NWAC closes is the common case. Turns All Year is the only community source that reports snow surface by aspect. CalTopo slope shading is the terrain input (link in the bundle's maps); without it the rating is a region-wide number with no route meaning. The NWS forecast, hourly, and grid URLs in each day's bundle 'nws' block ('forecast_url', 'hourly_url', 'grid_url') are the ones to read; fetch them as given and never re-resolve the point from another coordinate.
Scripts: python3 ${AV}/avalanche.py {zone|forecast} LAT LON, python3 ${WX}/snotel.py {nearest LAT LON --elev FT|data TRIPLET --days 3}, python3 ${WX}/nws.py grid LAT LON (snow level, snowfall).`,
  clock: `Pace models by terrain type (trail, snow, glacier, scramble), turnaround discipline, how daylight and freeze-thaw set the start. Compute from the published route stats in the itinerary and the bundle's 'sun' block; tides and ferry sailings in the bundle are fixed windows. State the pace assumptions as readings so they can be checked. 'margin_hours' is the window minus the plan; a negative margin is a red reading with the cut it implies. Every row's return time is start plus moving plus stops, computed and stated, and falls after its turnaround; check every row against this before returning.
Script: python3 ${WX}/sun.py LAT LON DATE --tz TZ when the bundle lacks it.`,
  contingency: `Which bailouts are real at each stage of the route, where cell coverage exists (carrier maps), satellite device, what SAR in that county needs from a caller, nearest hospital, who holds the itinerary. Judgment plus search, no feed. A lost bail option is a reading with a consequence, never a footnote. The nearest hospital, its road distance, and the SAR contact each carry a source URL or are listed in 'gaps'; "from memory" is not a reading.`,
}

const QUESTION = {
  access: 'Can I get to the trailhead, and am I allowed to be there?',
  sky: 'What will the weather do, each day, and how sure is that?',
  ground: 'What is the route like right now?',
  snow: 'Is the snowpack a threat on this terrain?',
  clock: 'How long will it take, and when do I have to leave and turn around?',
  contingency: 'What happens if it goes wrong?',
}

function researchPrompt(name, bundles, extra = '') {
  return `You are the ${name[0].toUpperCase() + name.slice(1)} agent of a PNW trip planner. Your question: ${QUESTION[name]}
${COMMON}
Domain knowledge you carry: ${KNOWLEDGE[name]}
${extra}
Daily location bundles: ${bundlesFor(bundles)}`
}

// -- hard stops are applied in code from the agents' normalized signals, so no synthesizer can argue one away
function hardStops(r) {
  const hits = []
  const add = (rule, day, evidence, clearing_time = '') => hits.push({ rule, day, evidence, clearing_time })
  for (const d of r.sky?.signals?.days ?? []) {
    if (!d.push_day) continue
    if (d.warning_products.length) add('nws_warning', d.date, `warning products on push day: ${d.warning_products.join(', ')}`)
    if (d.lightning) add('nws_warning', d.date, 'lightning forecast on push day')
    if (d.whiteout) add('nws_warning', d.date, 'forecast whiteout at route elevation on push day')
    if (d.max_gust_mph_exposed > 35) add('nws_warning', d.date, `gusts ${d.max_gust_mph_exposed} mph at the exposed elevation on push day`)
  }
  for (const d of r.snow?.signals?.days ?? []) {
    if (d.max_level_touched >= 4) add('avalanche_high', d.date, `danger level ${d.max_level_touched} in ${d.bands_touched.join('/')} (${d.zone})`)
  }
  const a = r.access?.signals
  if (a) {
    if (!a.road_open && !a.alternative_route_to_trailhead) add('road_or_closure', 'all', 'road closed with no documented alternative to the trailhead')
    if (a.closure_order_on_route) add('road_or_closure', 'all', 'closure order covers the route')
    if (a.permit_required && !a.permit_available && !a.permit_walkup_path) add('permit_unavailable', 'all', 'required permit unavailable with no walk-up path')
    if (a.fire_perimeter_or_evacuation_on_route) add('fire_or_volcano', 'all', 'fire perimeter or evacuation level over the route or its exit')
    if (!['Normal', 'n/a', ''].includes(a.volcano_alert_level)) add('fire_or_volcano', 'all', `volcano alert level ${a.volcano_alert_level}`)
  }
  const g = r.ground?.signals
  if (g?.glaciated && (g.latest_route_report_age_days < 0 || g.latest_route_report_age_days > 14) && !g.webcam_or_satellite_confirmed) {
    add('glacier_no_report', 'all', `glaciated route, latest report ${g.latest_route_report_age_days < 0 ? 'none' : g.latest_route_report_age_days + ' days old'}, no webcam or satellite confirmation`)
  }
  for (const [name, out] of Object.entries(r)) {
    for (const h of out?.hard_stops ?? []) if (h.triggered) add(h.rule, h.day ?? 'all', `${name}: ${h.evidence}`, h.clearing_time ?? '')
  }
  const seen = new Set()
  return hits.filter((h) => { const k = `${h.rule}|${h.day}|${h.evidence}`; if (seen.has(k)) return false; seen.add(k); return true })
}

function floorFrom(hits) {
  if (!hits.length) return null
  return hits.some((h) => h.clearing_time) ? 'Wait' : 'No-go'
}

function routeChange(...outs) {
  const c = outs.map((o) => o?.route_change).filter((rc) => rc && (rc.start_moved || rc.added_miles > 0 || rc.added_gain_ft > 0))
  return c.length ? c : null
}

let bundles = args.bundles
if (!bundles) {
  log('no bundles passed; resolving the itinerary')
  const r = await agent(`Run the daily location resolver and return its output unchanged.
Write this itinerary JSON to ~/.cache/pnw-outdoor-planner/itinerary.json, then run:
  python3 ${SKILL}/scripts/resolve.py ~/.cache/pnw-outdoor-planner/itinerary.json --today ${today}
Return the parsed JSON under 'bundles'. On a non-zero exit, return the stderr under 'error' and an empty object under 'bundles'.
Itinerary: ${JSON.stringify(itinerary)}`,
    { label: 'resolve', phase: 'Resolve', model: 'sonnet', effort: 'low', schema: { type: 'object', properties: { bundles: { type: 'object' }, error: { type: 'string' } }, required: ['bundles'] } })
  if (!r || r.error) throw new Error(`resolver failed: ${r?.error ?? 'agent returned null'}`)
  bundles = r.bundles
}
const days = bundles.days
for (const w of bundles.warnings ?? []) log(`registry warning: ${w}`)

const research = (name, model, effort, extra) =>
  agent(researchPrompt(name, days, extra), { label: name, phase: 'Research', model, effort, schema: agentSchema(SIGNALS[name]) }).catch(() => null)

const runSnow = trip.snow || args.force_snow
if (!runSnow) log('snow false on this trip; Snow agent skipped')

const skyP = research('sky', 'sonnet', 'medium')
const snowP = runSnow ? research('snow', 'opus', 'high') : Promise.resolve(null)
const contingencyP = research('contingency', 'sonnet', 'low')
const clockP = research('clock', 'sonnet', 'medium')

// -- Access and Ground are the two that can move the start or the distance, so Clock waits on them alone
const [access, ground] = await parallel([() => research('access', 'sonnet', 'medium'), () => research('ground', 'sonnet', 'medium')])
let clock = await clockP
const change = routeChange(access, ground)
if (change) {
  log(`route changed by Access or Ground: ${change.map((c) => c.reason).join('; ')}; re-running Clock`)
  clock = await agent(researchPrompt('clock', days, `Re-run: the start point or distance changed after your first pass. Apply these changes before anything else: ${JSON.stringify(change)}. Your first pass, for reference: ${JSON.stringify(clock?.signals ?? null)}`),
    { label: 'clock (re-timed)', phase: 'Re-time', model: 'sonnet', effort: 'medium', schema: agentSchema(SIGNALS.clock) }).catch(() => clock)
}
const [sky, snow, contingency] = await Promise.all([skyP, snowP, contingencyP])

const results = { access, sky, ground, snow, clock, contingency }
const missingAgents = Object.entries(results).filter(([n, v]) => v === null && (n !== 'snow' || runSnow)).map(([n]) => n)
if (missingAgents.length) log(`agents that returned nothing: ${missingAgents.join(', ')}; the verdict will name them as gaps`)

const hits = hardStops(results)
const floor = floorFrom(hits)
if (floor) log(`hard stop: verdict floor is ${floor} (${hits.map((h) => h.rule).join(', ')})`)

const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['Go', 'Go with caveats', 'Wait', 'No-go'] },
    reassess_by: { type: 'string' },
    deciding_factors: { type: 'array', items: { type: 'string' } },
    reasoning: { type: 'string', description: 'the chain, one short paragraph, every link cited to an agent reading' },
    flip: { type: 'string', description: 'what would flip the verdict' },
    morning_check: { type: 'string' },
    anchors_used: { type: 'array', items: { type: 'string' } },
    report_markdown: { type: 'string' },
  },
  required: ['verdict', 'reassess_by', 'deciding_factors', 'reasoning', 'flip', 'morning_check', 'anchors_used', 'report_markdown'],
}

function synthesisPrompt(revision = '') {
  return `You are the orchestrator of a PNW trip planner writing the verdict and the report. Six research agents have returned their tables; you do the only synthesis. Agents never talk to each other, so cross-agent conflicts (Sky clear, Ground says the snow bridges are gone) are yours to resolve.
${COMMON}
Hard stops already applied in code: ${JSON.stringify(hits)}
Verdict floor from them: ${floor ?? 'none'}. ${floor ? `Your verdict cannot be better than ${floor}; you may make it worse.` : 'Any Go you reach can still be lowered by the weighed assessment.'}
Agents that returned nothing: ${JSON.stringify(missingAgents)} (name each as a gap in the report; a missing Ground or Sky on a committing route is itself a reason to Wait).

The weighed assessment, past the hard stops, is a protocol for judgment, never a grid of thresholds:
1. Enumerate every amber and red reading from every agent, plus every unclassified reading. Nothing is dropped for lacking a rubric row.
2. State each reading's consequence on this route, in the route's own terms: miles, hours, exposure, a lost bail option, a crossing that is now committing.
3. Weigh likelihood and age from the source's confidence and as-of. A week-old report on a fast-changing factor counts for less than a webcam from this morning.
4. Weigh reversibility: whether the hazard can be seen and retreated from on the day, or commits the party before it shows. An irreversible factor with low confidence is weighed at its worst plausible reading.
5. Weigh interactions: new snow plus wind plus the route's lee aspect; smoke plus heat plus a long dry section; a bridge out plus daylight plus the turnaround. Clock's re-timed figures are in its table when Access or Ground moved the start.
6. Calibrate to the party stated above.
7. Verdict and chain: Go, Go with caveats (each caveat a named condition, such as "go if the 5 a.m. NWAC update holds Moderate on north aspects"), Wait (with the trigger and the reassess date), or No-go.

Anchors, a comparison the verdict names, never a rule it applies: avalanche Low to Moderate comfortable, Considerable terrain-dependent; summit-push wind under 25 mph and good visibility comfortable, 25 to 35 mph or partial visibility caveat territory; route and glacier report recent with no hazard comfortable, over a week old or one hazard flagged caveat territory; road and trailhead open with permit in hand comfortable, road exposed to closure or permit pending caveat territory. State which anchors you leaned on and which readings had none.

report_markdown is the full report, in this order, no source dump without a verdict on top:
1. Executive summary: the one-line verdict (Go / Go with caveats / Wait, reassess by [date] / No-go), the deciding factors, the reasoning paragraph, what would flip it, and the one thing to check on the morning of.
2. Forecast: day by day (single day for a day trip), each day tagged with Sky's confidence tag and why.
3. Factor detail: one line per applicable factor with its current reading, what it means for this route, and its color, linked to its source; unclassified readings listed with the rest; hard stops marked.
4. Timing: Clock's start, turnaround, and margin per day with the pace assumptions.
5. Contingency: bailouts, coverage, SAR contact, who holds the itinerary.
6. Map and route: the links in the bundles' 'maps' block (Gaia, CalTopo when snow) and a note that the GPX is produced by the skill.
7. Interactive maps: the NWCC SNOTEL map when the snow factor applies, Watch Duty in fire season or with any active incident within a day's smoke drift, and every unit map in 'maps.unit_maps'; each as a link with one line on what to look for.
8. Sources: every URL any agent opened, with its catalog id or "ad hoc" and its as-of.
Markdown mechanics: no em dashes; italics with single asterisks; never truncate a line.

Bundles: ${bundlesFor(days)}
Maps: ${JSON.stringify(bundles.maps ?? {})}
Agent returns: ${JSON.stringify(results)}
${revision}`
}

let verdict = await agent(synthesisPrompt(), { label: 'synthesis', phase: 'Synthesize', model: 'opus', effort: 'high', schema: VERDICT })
if (!verdict) throw new Error('synthesis returned null')

const CRITIC = {
  type: 'object',
  properties: { ok: { type: 'boolean' }, missing: { type: 'array', items: { type: 'string' } } },
  required: ['ok', 'missing'],
}
const critic = await agent(`Completeness check on a trip report. Answer only what is missing; do not judge the verdict.
Check, and list each failure as one line:
- every catalog id in each bundle's 'sources' list has a reading from some agent, or appears in an agent's 'gaps';
- every itinerary day has a forecast confidence tag in the Forecast section;
- every amber or red reading and every unclassified reading from the agent returns appears in Factor detail;
- every hard stop in the list below is named in the executive summary;
- the Interactive maps section carries the NWCC map when snow applies, Watch Duty when fire season or an incident applies, and each unit map in the maps block;
- the Sources section lists every URL in the agents' 'sources_opened'.
Hard stops: ${JSON.stringify(hits)}
Bundles: ${bundlesFor(days)}
Maps: ${JSON.stringify(bundles.maps ?? {})}
Agent returns: ${JSON.stringify(results)}
Report: ${verdict.report_markdown}`,
  { label: 'critic', phase: 'Check', model: 'sonnet', effort: 'low', schema: CRITIC })

if (critic && !critic.ok && critic.missing.length) {
  log(`critic found ${critic.missing.length} gap(s); one revision pass`)
  const revised = await agent(synthesisPrompt(`Revision: a completeness check found these gaps in your report; fix each and return the whole report again:\n${critic.missing.map((m) => `- ${m}`).join('\n')}`),
    { label: 'synthesis (revised)', phase: 'Check', model: 'opus', effort: 'medium', schema: VERDICT })
  if (revised) verdict = revised
}

const ORDER = ['Go', 'Go with caveats', 'Wait', 'No-go']
if (floor && ORDER.indexOf(verdict.verdict) < ORDER.indexOf(floor)) {
  log(`synthesizer returned ${verdict.verdict} above the hard-stop floor; verdict set to ${floor}`)
  verdict.verdict = floor
}

return {
  verdict: verdict.verdict,
  reassess_by: verdict.reassess_by,
  deciding_factors: verdict.deciding_factors,
  reasoning: verdict.reasoning,
  flip: verdict.flip,
  morning_check: verdict.morning_check,
  anchors_used: verdict.anchors_used,
  hard_stops: hits,
  report: verdict.report_markdown,
  agents: results,
  missing_agents: missingAgents,
  critic,
  bundles,
}
