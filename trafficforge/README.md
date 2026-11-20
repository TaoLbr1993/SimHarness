# TrafficForge

**English** · [简体中文](README.zh-CN.md)

**Turn a single sentence into a traffic simulation scenario.**

TrafficForge, formerly NL2MAP, is the traffic scenario authoring module in SimHarness. Describe a
scenario in natural language or select an area on a map to generate a synthetic road network or
import real-world roads. TrafficForge adds vehicle counts, target traffic conditions and departure
plans, then presents an editable preview. After confirming and adding the scenario, you can start
the simulation in the main system.

```text
Describe a scenario / Select a map area → Generate roads and traffic demand → Preview / Edit settings → Confirm and add → Start the simulation
```

Generating a preview does not run a vehicle simulation or automatically add the draft to the
scenario catalog. Congestion targets, density and vehicle counts in the preview are scenario
settings; actual speed, queues and congestion must be evaluated after a run.

## What you can generate

| Example input | Processing |
|---|---|
| `Generate a four-way intersection with traffic lights on all four approaches` | The model writes roadgen code; the service compiles a SUMO network, checks XML rules and visual evidence, and feeds problems and reasons back for repair |
| `生成北航学院路校区附近 1 公里路网，测试拥堵场景` (roads within 1 km of Beihang's Xueyuan Road campus for a congestion test) | Amap locates the Chinese place name, OSM supplies roads, SUMO compiles the network, and TrafficForge generates demand |
| `Generate roads within 1 km of Times Square, Manhattan, New York, USA` | Photon locates the English place name, OSM supplies roads, and SUMO compiles the network |
| Select an area on the map, optionally adding `Schedule 120 vehicles over 10 minutes` | OSM roads are imported using the selected bounds; text adds scenario requirements without allowing the model to relocate the area |
| `Test vehicle speeds in congested traffic, with 120 planned departures over 10 minutes` | When no map is specified, the service creates a default signalized intersection and generates demand, duration and targets to evaluate |

- Maps show main roads by default; explicitly request minor roads, lanes or signal details for a detailed version.
- Traffic mode displays vehicle totals and departure rates; autonomy mode also supports requested L0–L5 shares.
- Previews support image zoom and edits to vehicle count, density, duration, warm-up, seed, flow, reference speed and automation shares.
- The interface defaults to **English** and can switch to **简体中文** while preserving input, the current draft and unapplied edits.
- Geocoding follows the original place text: English uses Photon and Chinese uses Amap. Interface language does not select the provider.
- Confirmed scenarios contain SUMO networks, vehicle routes and a `.sumocfg` file that SimHarness can load.

After the first preview, use the map edit field at the top. Each request acts on the current draft. For example, enter `Add an east-west road`, then `Remove the east-west road`. TrafficForge attaches a two-way road to an available junction and rebuilds SUMO connections and vehicle routes. Removal targets the most recently added road in that direction first, otherwise the matching arms at the junction nearest the map center.

Use `Show only main roads`, `Show secondary roads`, or `Show all roads` to filter the preview. The filter uses the roads already in the draft. For OSM maps it uses highway classes, and for unclassified synthetic networks “main roads” uses the highest edge priority. The network and departures used by the simulation stay the same when changing the display. The API accepts one instruction per `POST /scenarios/{task_id}/map-edits` with fields `instruction` and optional `ui_language`. Confirm the edited preview to publish it.

## Quick start

Run all commands below from the **SimHarness repository root**. The desktop launcher requires an
already configured Windows `.venv`, SUMO and WSL generation environment. It starts or reuses
services; it does not install these dependencies.

```powershell
# English by default; generation service on 8001, Core API on 8000
./scripts/dev/start_trafficforge.ps1

# Open in Chinese
./scripts/dev/start_trafficforge.ps1 -Language zh

# Start background services only
./scripts/dev/start_trafficforge.ps1 -NoUi

# Once the services are ready, open the autonomy scenario authoring interface
.venv/Scripts/python.exe scripts/dev/open_trafficforge.py --mode autonomy
```

Describe your requirements in the TrafficForge input box, then review the generated map and
scenario settings. Use **Apply all changes** to apply parameter edits. Only **Confirm and add scene**
adds the draft to the catalog; you decide when to start it in Simulation setup.

### Start with a map selection

1. Click **Select map area** on the simple entry page to open the map dialog.
2. Search for a place and choose a candidate, or pan and zoom the base map directly. Searching moves the viewport; it does not select an area.
3. Click **Draw area**, drag a rectangle and check its extent and dimensions. Redraw it if needed.
4. Click **Use this area** to return to the input page. Add vehicle counts, traffic conditions or duration, or leave the text blank to use default scenario settings.
5. Click **Generate preview**, inspect the generated roads and departure plan, then confirm to add the scenario.

Canceling leaves the original input and selection unchanged. After changing or clearing the area,
generate a new preview before confirming; the old preview cannot be added as the new area.
Only main roads are imported by default. The default maximum selection diagonal is 6 km; draw a
smaller rectangle if it exceeds the limit. The system does not silently shrink the selection.
You can request minor roads or greater detail in the text. After generating a draft, use the map
edit field to add or remove east–west or north–south roads, or change the preview’s road filter.

The base map uses online OpenStreetMap tiles and the repository's local Leaflet scripts, with
WGS84 coordinates throughout. If tiles fail to load, check the connection and retry. Generation
also requires access to the OSM road service. Chinese searches still use Amap and English searches
use Photon. Manual selection without a search does not depend on geocoding or an Amap key.
An area-only request with no text uses deterministic defaults without calling a model; additional
text requirements are parsed by the configured model.

### Drafts from the command line

The natural-language workflow is also available through the CLI. `generate` creates a draft by
default; `publish` explicitly confirms that it should be added:

```powershell
.venv/Scripts/python.exe trafficforge/scene_cli.py generate "生成北航学院路校区附近1公里路网，计划发出120辆车"
.venv/Scripts/python.exe trafficforge/scene_cli.py publish <task_id>
.venv/Scripts/python.exe trafficforge/scene_cli.py list
.venv/Scripts/python.exe trafficforge/scene_cli.py show <task_id>
```

The Chinese example requests roads within 1 km of Beihang's Xueyuan Road campus and 120 planned
departures. Its original Chinese place name selects Amap.

API documentation: [http://127.0.0.1:8001/docs](http://127.0.0.1:8001/docs).
Health check: [http://127.0.0.1:8001/health](http://127.0.0.1:8001/health).

## Dependencies and configuration

The desktop and Core use the repository's Python 3.10 environment; the generation service uses a
separate Python environment. The current Windows launcher reuses the `SimHarness-Ubuntu` WSL
distribution, the existing `nl2map` user and `/opt/nl2map-venv/bin/python`. These historical runtime
resource names were retained during the rename to avoid reinstalling the environment.

Generation dependencies are listed in [backend/requirements.txt](backend/requirements.txt),
including FastAPI, an OpenAI-compatible client, roadgen, Pillow, pyproj and pypinyin. roadgen
provides a Linux wheel, so Windows uses WSL. Both `sumo` and `netconvert` must be on the generation
environment's PATH. Natural-language place or scenario parsing requires an OpenAI-compatible text
model. An area-only request with blank text can use defaults without a model. The default visual
review of synthetic networks additionally requires image input support.
roadgen source: [hdmap_generator](https://github.com/hakuturu583/hdmap_generator).

| Setting | Purpose |
|---|---|
| `MODEL_BASE_URL` / `MODEL_API_KEY` / `MODEL_NAME` | Model connection settings for the generation service |
| `OPENAI_API_KEY` | Optional Windows key forwarded to WSL as `MODEL_API_KEY`. A model can also be configured in the backend’s local `.env`. |
| `AMAP_WEB_API_KEY` | Amap Web Service key required for Chinese place queries; the public Photon API needs no key |
| `MAP_GEOCODER_PROVIDER=auto` | Choose the provider from the original place text, or explicitly set `amap` or `photon` |
| `MAP_PHOTON_URL` / `MAP_OVERPASS_URL` | Geocoding and road service endpoints, including self-hosted instances |
| `TRAFFICFORGE_API_URL` | Generation service URL for the desktop and CLI; defaults to `http://127.0.0.1:8001` |

See [backend/.env.example](backend/.env.example) for the full backend configuration template. Keep
real keys only in the local environment or an untracked `.env`. Set `TRAFFICFORGE_API_URL` in the
desktop or CLI process environment. The legacy `NL2MAP_API_URL` remains a fallback; the new variable
takes precedence. The main project's `trafficforge-preview` extra supplies preview dependencies,
not the full backend requirements.

To start the generation service in a separate Linux environment, install SUMO, then prepare the
Python dependencies and model configuration:

```bash
cd trafficforge/backend
python -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
# Set MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME in the local .env; add an Amap key for Chinese queries
.venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8001
```

## Directory structure

```text
trafficforge/
├── README.md                 # English guide and API details (default)
├── README.zh-CN.md           # Chinese guide and API details
├── scene_cli.py              # Draft generation, explicit publication and scenario queries
└── backend/
    ├── main.py               # Standalone FastAPI generation service
    ├── agent.py              # Intent parsing, place selection, roadgen code and review
    ├── realmap.py            # Real-world road import and validation
    ├── map_sources.py        # Language-based geocoding and OSM road retrieval
    ├── map_area.py           # WGS84 rectangles and extent validation
    ├── amap_source.py        # Amap queries and coordinate conversion
    ├── runner.py             # Execute roadgen code and compile networks
    ├── review_pipeline.py    # XML rules, visual evidence and repair feedback
    ├── scenario_pipeline.py  # Scenario demand and draft orchestration
    ├── scenario_revision.py  # Reuse the map and rebuild demand in a new draft
    ├── scenario_catalog.py   # Explicit publication and legacy scenario reads
    ├── renderer.py           # Preview rendering; bilingual labels/refresh in preview_*.py
    ├── tests/                # Offline regression tests
    ├── requirements.txt      # Standalone backend dependencies
    ├── .env.example          # Configuration example without real credentials
    └── workspaces/           # Drafts, sources, previews and logs; not committed
```

Desktop widgets live in the main project's `ui/`, official launchers in `scripts/dev/`, and
confirmed scenarios in `configs/maps/nl2-<task_id>/`. Existing `nl2-` scenario IDs are preserved so
historical configurations and experiment references continue to work. New manifests use
`schema_version: "2.0"` and place scenario summaries in `spec.trafficforge`. Readers also support
legacy `schema_version: "1.0"` / `spec.nl2map`; existing published packages are not rewritten.
Old launcher scripts and the `nl2map-preview` extra remain compatibility entry points. The current
source and draft directory is `trafficforge/`.

The following sections document network checks, real-map sources and scenario APIs in detail.

## Synthetic network generation and validation

```text
Natural-language request ("Generate an intersection")
        ↓
  FastAPI service (main.py)
        ↓
  Extract acceptance criteria with quotes from the request (fixed across retries)
        ↓
  Agent writes Python code that calls roadgen (agent.py)
        ↓
  Execute code in a separate task directory (runner.py)
        ↓
  roadgen exports SUMO PlainXML → netconvert compiles and checks .net.xml
        ↓
  Render the full network, a junction close-up and signal icons for controlled lanes
        ↓
  Execute XML acceptance rules + model-assisted visual geometry review
        ↓
  Combine confirmed structural problems, suspected visual problems and unverified requirements
        ↓
  On failure, return code, images, problems and reasons for regeneration (up to three generation attempts in total)
        ↓
  Return each attempt, check results and generated files
```

## API reference

### POST /generate

Request:

```json
{ "description": "A four-way intersection where two two-way, two-lane roads cross at right angles" }
```

Since Demo 0.2.0, the optional `review_mode` defaults to `rules_and_image`. The model first turns
the request into acceptance criteria backed by quotes from the original text. Code then parses
the compiled XML and evaluates those rules. Every valid generation still produces images for a
supporting model check of road shapes, obvious misalignment and abnormal reversals.

`image_and_facts` and `image_only` retain their original behavior for comparison experiments;
they do not use the newer requirement-rule checks. With explicit `image_only`, the requirement
review model receives only the request, generic image-reading instructions and PNG images. It
does not receive a network summary, XML, generated code or previous review conclusions. For example:

```json
{ "description": "Generate a four-way intersection. Traffic lights are required.", "review_mode": "image_only" }
```

`image_only` still runs `netconvert` and basic structural checks locally, but does not send their
facts to the review model. The generation/repair model still receives the request, previous code,
problem reasons and available images. Each review is a separate model call;
`review.evidence_mode` records the mode actually used in that round.

The default acceptance rules cover:

| Rule | What the program checks |
|---|---|
| `junction_count` / `approach_count` | Junction count and the number of adjacent road branches at each junction |
| `signals` | Presence/absence of effective signal control across the network, or effective controlled connections for every turn and incoming lane on specified approaches |
| `lane_count` | Incoming and outgoing lane counts for each selected direction, checked separately |
| `turn` | At least one specified turning movement exists from the selected approach, or that turn is entirely prohibited |
| `connected` | Whether the road graph is weakly connected; this does not imply vehicle reachability between arbitrary points |
| Fixed integrity checks | Road/lane references, connection endpoints, nonempty signal programs with consistent phase lengths, positive durations and valid phase indices |

The existence of one `tlLogic` does not establish four-direction coverage; one controlled turn on
an approach is insufficient too. A general request for "traffic lights" means signal control.
Physical pole counts/positions and signal timing safety are outside these rules.
Directions use adjacent nodes' SUMO XY coordinates (north = +Y, east = +X). Explicit directional
rules become unverified when an approach deviates more than the configured 30° threshold from an
axis or multiple approaches lie on the same side. Parallel roads between the same two nodes are
not silently summed into a single approach. When no direction is specified, `all_approaches`
checks actual incoming approaches; it does not require an incoming signal on an outgoing-only
one-way branch. If the request explicitly requires a controlled incoming approach in a given
direction, absence of that approach fails the check. `scope=single` requires a unique junction;
ambiguous targets in a multi-junction network remain unverified rather than passing an arbitrary one.

Response:

```json
{
  "status": "success",
  "description": "...",
  "review_mode": "rules_and_image",
  "model_configured": true,
  "review_plan": {"rules": [], "visual_requirements": ["..."], "unverified_requirements": []},
  "generated_code": "...",
  "execution": {
    "exit_code": 0,
    "stdout": "",
    "stderr": "",
    "elapsed_sec": 1.23,
    "artifacts": ["output/validated.net.xml", "output/review.png"],
    "timeout": false
  },
  "validation": {"passed": true, "issues": [], "network": "output/validated.net.xml", "image": "output/review.png"},
  "rule_review": {"passed": true, "source": "compiled_xml", "checks": [], "approaches": []},
  "visual_review": {"source": "image", "certainty": "suspected", "issues": []},
  "review": {"passed": true, "issues": [], "issue_details": []},
  "attempts": [{"attempt": 1, "generated_code": "...", "execution": {}, "validation": {}, "review": {}}]
}
```

`success` requires a configured model, successful execution of generated code, existing and
parseable PlainXML files, a nonempty network compiled by `netconvert`, and successful image
rendering. The default mode additionally requires passed acceptance rules, no unverified
requirements and no unresolved visual concerns. The two older experimental modes still rely on
their model review to decide whether requirements are satisfied.

`attempts` preserves each generation's code, execution result, structural checks and requirement
checks. Failed-check diagnostics feed the next generation attempt. The default mode returns
`review_plan` (the interpreted requirements for inspection), `rule_review` (per-rule
pass/fail/unverified results and XML evidence), `visual_review` (raw visual concerns), and the
combined `review`. XML validity is not accepted merely because a model claims it.
Each `review.issue_details` entry includes `problem` and `reason`. The default mode also records
`source` and `certainty`: an XML rule failure is `xml/confirmed`, while a visual finding is
`image/suspected`. `review.issues` retains combined problem-and-reason strings for compatibility.
When an image is available, retries receive it together with the reasons. Code failures or
uncompilable networks have no image and use textual diagnostics. Unsupported image input or a
rendering failure does not fall back to text-only review and report a pass.

Timeouts, execution failures and failed checks return `execution_timeout`, `execution_error` and
`validation_error`, respectively. An unconfigured model or failed generation-model call returns
`agent_error`. Visual-review call or response-format failures return `validation_error` while
preserving completed XML checks. Unsupported requirements, ambiguous check targets and unresolved
purely visual concerns after retries return `review_required`; they are not reported as success
or confirmed structural errors.

Conflicting structural findings are not resolved by a model assertion alone. Automatic
adjudication currently covers only precisely matched signal-control predicates and spatial scopes:
presence/absence/coverage must match, and coverage must refer to the same unique junction and
required directions. For example, a passed network-wide signal-presence check cannot dismiss a
concern about an uncontrolled eastern approach. Precisely matched concerns remain in
`review.adjudications` with their XML evidence; the repair model receives only confirmed problems
and passed rules. Other structural concerns that cannot be matched exactly remain unverified.
Visual geometry concerns may trigger repair but are not relabeled as confirmed facts.

Interpreting natural-language acceptance criteria still depends on the model. Original-text quotes
prevent fabricated sources but do not prove that no requirement was omitted or misunderstood.
Invalid acceptance JSON, fields or quotes trigger feedback and at most one corrective retry. A
second invalid result still fails; the first failure is logged and cannot bypass later checks.
Callers can inspect `review_plan`. The current rules do not guarantee fully correct traffic rules,
semantics or simulation behavior.

Images are 2D schematics drawn from compiled SUMO geometry, with only one junction enlarged.
Missing lane widths use a rendering default. Three-color signal icons mark non-internal incoming
lanes whose `connection.tl` refers to a `tlLogic` program with phases. Multiple turns from one lane
share one icon. A `junction.type=traffic_light` alone, or a controller without a lane binding, does
not produce an icon. A leader line connects each icon to its lane. It indicates a controlled
connection, not control of every turn or a real pole location. The three-color icon is a static
symbol, not the current signal state, simultaneous release or valid timing. Both signal-free and
signal-equipped images use the same legend without verdict labels.
Images do not represent elevation or signal phases. They cannot establish exact widths, overpass
heights, all junction details or signal safety. Image-only review can test visible missing-light
cases but cannot replace structural or simulation checks.

The current network summary is limited to 64 non-internal roads, 64 non-internal nodes,
128 connections and 64 traffic lights; exceeding a limit rejects automatic assessment. Each attempt
has its own directory, but the subprocess is not a security sandbox: generated code still runs
with the service user's permissions. Do not expose this endpoint to untrusted users. Variables
containing API_KEY, TOKEN, SECRET, PASSWORD or CREDENTIAL are removed from the child environment;
this is not complete isolation. Requirements, generated code, diagnostics, network images and
summaries are sent to the configured model service and may contain local paths or other details.
Confirm that the service is appropriate for this content. Model-call exceptions during generation
or requirement review end the request immediately rather than entering code-repair retries.

## Real-world road networks (experimental, v0.3)

Without `selected_bounds`, `POST /generate` defaults to `source_mode="auto"`: the model distinguishes
abstract junction requests from real places. For example, this Chinese request asks for a real
road network within an approximately 1 km radius of Beihang's Xueyuan Road campus:

```json
{
  "description": "生成北京航空航天大学学院路校区附近约 1 公里半径的真实路网信息作为测试"
}
```

The real-place workflow is: extract the place and extent → choose Amap / Photon from the original
place text and query actual candidates → select a candidate ID by name, city and campus → retrieve
OpenStreetMap roads and turn restrictions through Overpass → compile with `netconvert` → check the
full XML network → load it in SUMO with empty demand → show a simplified preview → let the user
judge suitability. The model does not generate coordinates or real-world road geometry.
Ambiguous places or an empty candidate set return `needs_clarification`, with `candidates` and a
reason. Refine the description or add `selected_place_id` to the same request using a returned
candidate ID (OSM or `amap:<hash>`); the service checks the candidate set again.

Explicit `source_mode="synthetic"` uses the original roadgen generation/check/repair workflow;
`real_map` requires a real place or explicit map selection. The older `review_mode` options apply
only to synthetic generation. Real maps use XML/load checks and user judgment, recorded as
`review_mode="xml_and_user"`. Intent parsing and candidate selection can still misunderstand a
request. Responses expose `intent`, `place_choice.reason`, `place`, coordinates and candidates for
inspection; model confidence does not replace place evidence.

Photon lookup prefers a known full name in the original language and combines primary and
alternative queries, with at most two queries of five candidates each, deduplicated by OSM ID.
Unrelated first-query results, such as office buildings or technology parks, do not block the
alternative lookup. Query provenance and failure diagnostics are retained. If ambiguity across
cities or campuses persists, the system requests clarification rather than fixing coordinates or
defaulting to the first candidate.

The default `detail_level="auto"` follows the request. Without an explicit detail requirement it
uses `main_roads`, importing OSM motorway/trunk/primary/secondary/tertiary roads and their link
roads, while omitting residential, minor and service roads. Here, "main roads" means the principal
road skeleton rather than a strict municipal classification. The overview shows road shapes,
connections and names, without lane markings or signal icons; SUMO files retain the lanes and
signal control needed for operation. Requests for minor roads, more detail, or lane/signal details
select `detailed`, importing all passenger-accessible roads and rendering a local detail view.
Explicit `detail_level="main_roads"` or `"detailed"` overrides language-based selection. A detailed
version expands the data and display, but does not imply that lane or signal data has been calibrated.

The default radius is 1000 m and is disclosed in the response. The accepted range is currently
100–3000 m; oversized requests are not silently reduced. The center is the selected geocoder's
representative point, disclosed in `place`, not a 1 km buffer around the campus boundary.
Overpass selects roads within a circle around that point; raw files preserve complete OSM ways and
related restrictions. Conversion first splits roads at OSM geometry nodes, then filters SUMO road
segments using a 36-point circle approximation and simplifies intermediate nodes. Retained
segments are not cut precisely at the circle, so the actual extent can exceed the radius.
`validation.statistics.converted_xy_bounds` records the actual SUMO XY extent. The full selection
polygon appears in both calls in `conversion-command.json`. Actual bounds are computed from the
final lane geometry. XML location metadata is recorded separately as `xml_location_xy_bounds`
and must not be treated as the true filtered extent. This is neither exact GIS clipping nor a
representation of campus walls.

Only motor-vehicle roads accessible to `passenger` are imported. Disconnected components are kept
and counted. The result does not guarantee legal access to campus/private roads or reachability
between arbitrary origins and destinations. OSM data may be incomplete or outdated. SUMO may
infer missing lane counts, speed limits, priorities, lane connections and signal timing.
`source_ways_with_lanes/maxspeed` counts annotations on source highway ways, not coverage of the
imported roads. Signal nodes and icons reflect SUMO bindings, not verified real poles or current timing.

Real-map checks do not use the synthetic branch's 64-road summary limit. All external roads, lane
connections and controllers participate in the existing integrity rules, followed by a SUMO load
check. These checks still do not establish correct traffic rules, conflict-free timing or
geographic accuracy. Real maps do not call a vision model to judge requirement satisfaction; the
user inspects the preview. Lost turn restrictions, unbuildable signal programs and source signals
without connections are retained in `validation.import_issues` and logs for inspection, but do not
independently block delivery. Additional road modification, lane, signal, CARLA or traffic-demand
requirements are listed as unverified in `unverified_requirements` and `intent.extra_requirements`;
they do not automatically rewrite real roads. There is no roadgen redesign fallback for this branch.

Output directory: `backend/workspaces/<task_id>/output/`.

| File | Purpose |
|---|---|
| `source.osm.xml`, `overpass-query.txt`, `geocoder-*.json` | Source data and actual queries |
| `validated.net.xml`, `map.sumocfg` | SUMO network and empty-demand load-test configuration |
| `map_preview.png`, `review.png` | Main-road overview by default; only detailed mode generates a local close-up with signal icons |
| `metadata.json` | Intent, place, source timestamps, cache status, validation, statistics and limitations |
| `conversion-command.json`, `netconvert-version.txt`, `netconvert.log`, `sumo-load.log` | Reproducible conversion arguments and full diagnostics |

The response's `artifact_urls` provides download paths through `GET /artifacts/<task_id>/<filename>`;
only files inside that task's output directory are accessible. `success` means that the network
and preview were generated and basic checks and loading completed. It does not establish real-world
accuracy or requirement satisfaction. `user_review="pending"` and `user_review_note` ask the user to
inspect suitability for the test. `source_error` denotes a map/geocoding service failure;
`validation_error` denotes a conversion, structural or loading failure. Metadata and diagnostics
are preserved wherever possible. Requests needing natural-language parsing without a configured
model return `agent_error`; area-only default scenarios do not require a model.
`validation.passed` means only that XML and SUMO loading passed; `visual_review=null` means no visual
review was called. Tool timeouts preserve partial output. Format, reference, conversion and
loading errors still fail the request.

The real-map branch uses the backend dependencies listed above. It needs `netconvert`, `sumo` and
network access to map services; natural-language parsing also needs the model service. SUMO XSD
data is discovered from the installation, or you can set `SUMO_HOME`. `.env.example` lists endpoints,
radius, timeout, size and cache settings. Public map-service requests are serialized within one
process and cached for 24 hours. Rate limits do not trigger immediate retries. Public demo services
are not a production SLA. Place names and search text are sent to the configured geocoder;
coordinates/radius or rectangular bounds go to the road service. The real-map branch sends only
the description and candidates to the model.

Sources and policies: [Photon](https://github.com/komoot/photon),
[Overpass public-instance guidance](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html),
[SUMO OSM import](https://sumo.dlr.de/docs/Networks/Import/OpenStreetMap.html), and the
[Nominatim public-service policy](https://operations.osmfoundation.org/policies/nominatim/).
Maps and images retain [© OpenStreetMap contributors / ODbL](https://www.openstreetmap.org/copyright)
attribution. Map-only requests remain independent tasks; generated scenarios enter the Core catalog
only after user confirmation, as specified in ADR-032. Current output is native SUMO scenarios;
CARLA mirroring is not yet provided.

### Rectangular selection API

Add `selected_bounds` to `POST /generate`. The four fields contain WGS84 longitude/latitude in degrees:

```json
{
  "description": "",
  "selected_bounds": {
    "west": 116.337,
    "south": 39.979,
    "east": 116.363,
    "north": 39.997
  },
  "output_kind": "scenario",
  "detail_level": "main_roads",
  "simulation_mode": "traffic",
  "ui_language": "en",
  "run_simulation": false,
  "publish_to_catalog": false
}
```

Blank `description` uses default scenario settings without a model. You can also add requirements
such as "Schedule 120 vehicles over 10 minutes". The explicit rectangle always determines the map
extent. Generation bypasses geocoding and model candidate selection; place names or radii in the
text do not replace the selection. `output_kind="map"` generates only a network. The desktop uses
`"scenario"` to continue with an unpublished departure plan and scenario draft.

Validation rules:

- All four coordinates must be finite numbers: `-180 ≤ west < east ≤ 180` and `-85 < south < north < 85`. Dateline-crossing areas are unsupported.
- The diagonal must not exceed `2 × MAP_MAX_RADIUS_M`, which defaults to 6000 m. The client reads `max_diagonal_m` from `GET /map/selection-config`; the backend checks it again before generation. Invalid extents are not automatically clipped down.
- Do not combine bounds with `source_mode="synthetic"`, `selected_place_id` or `map_task_id`; the request must identify one map source.
- Without a selected area, a nonempty description is still required. Existing place-plus-radius and roadgen requests keep their behavior.

The selection directly drives the Overpass rectangular road query and `netconvert` geographic
filter, retaining complete road segments that intersect the area. Roads can extend slightly
beyond the rectangle; this is not exact GIS clipping. Map metadata records the bounds, sources and
actual conversion details. The preview pin marks the **area center**, not a claimed search place.
The scenario response's `selected_bounds` and later parameter revisions preserve the same area;
changing vehicle counts does not select a new location or download the map again.

The map dialog uses two read-only endpoints:

| Endpoint | Response and purpose |
|---|---|
| `GET /map/selection-config` | `{"max_diagonal_m": 6000.0}`; the actual value comes from the backend map configuration |
| `GET /map/places?q=<place_or_address>` | `candidates` and `warnings`; selects the geocoder from the original query language and moves the viewport after the user chooses a candidate |

Place searches need no model and do not generate roads, publish scenarios or start a simulation.
Amap credentials remain on the backend. The base map and search candidates only help select a
geographic extent; the simulation network is compiled from the actual OSM data.

### Chinese and English address lookup

Create a **Web Service** key on the Amap developer platform with geocoding and POI search permissions.
Set it as `AMAP_WEB_API_KEY` in the local user/system environment or backend `.env`; do not commit it.
The Windows launcher checks process, user and machine environments in that order and forwards the
value to the WSL backend child process. Restart the generation service after changing configuration.
`GET /health` reports key presence in `amap_configured` and the configured routing mode in
`geocoder_provider` (`auto` for language-based routing). Neither field returns the key.

The default `MAP_GEOCODER_PROVIDER=auto` routes by the **original place/address text**: English
uses Photon and Chinese uses Amap. Mixed text containing Han characters follows the Chinese route.
The source is the `location_quote` from the request, not interface language, surrounding instruction
text or a model-generated translation. For example, `生成 Stanford University 周边路网` uses Photon,
while `Generate roads near 北京航空航天大学` uses Amap. The public Photon API needs no API key.
A Chinese address without an Amap key produces an explicit error; it does not fall back to Photon.

Set `amap` or `photon` explicitly to fix the provider. Optional `MAP_AMAP_CITY` supplies a city
constraint, such as Beijing, for Amap queries only. Candidate `provider` fields and source records
identify the service actually used. Amap authentication, permission, quota and network errors are
shown directly without silently switching geocoders. If geocoding has already returned valid
candidates and only POI permission is missing, candidates are preserved with an explanation.
No-match results still require more information; not every building is necessarily indexed.

Amap provides location lookup only; nearby roads still come from OSM. Results retain the returned
address, match granularity, original GCJ-02 coordinates and the approximate WGS84 conversion note
used for Overpass queries. This approximation is not survey-grade accuracy; the user must inspect
the map. Province, city or district matches cannot stand in for a building. Keys are not written to
source records, caches, scenario files or errors. A real-map preview marks the matched place with a
red "Original location" pin and its name. The tip corresponds to the actual query center, aligned
using the SUMO projection and offset. Simplified, detailed and enlarged previews retain the marker;
it is not a vehicle spawn point. Without a real key, only offline protocol and failure handling can
be validated; a live Amap lookup cannot be claimed.

Official documentation: [Geocoding](https://developer.amap.com/api/webservice/guide/api/georegeo),
[Place search](https://developer.amap.com/api/webservice/guide/api-advanced/search), and
[Create a Web Service key](https://lbs.amap.com/api/webservice/create-project-and-key).

## Scenario authoring interface (v1.2)

TrafficForge defaults to **English**. Switch to **简体中文** at the top right without losing input,
the current draft, map or unapplied edits. Switching language does not regenerate, publish or run a
scenario. English images prefer source OSM `name:en` labels; absent English names use pinyin with
English road/place types. Chinese images retain original names. English labels are for display
only; they do not change source OSM, the SUMO network or coordinates. User text and source
diagnostics retain their original content. Existing real-map language images are refreshed on read
using the current rules, avoiding stale Chinese-label caches. Offline transliteration uses a
pinned `pypinyin` version included in backend requirements and the main project's optional lock.
`scripts/dev/start_trafficforge.ps1` opens the simple English entry on Windows; use `-Language zh`
for Chinese. Traffic mode and L0–L5 autonomy mode both support the two languages.

The default workflow is **describe a scenario or select a map area → generate a preview → confirm
and add → open Simulation setup → start manually**. Generation does not trial-run vehicles.
"Congestion" is a target setting; without a run, no measured speed/density or congestion verdict is
presented.

From the project root on Windows:

```powershell
./scripts/dev/start_trafficforge.ps1
```

The launcher reuses the WSL roadgen environment, SUMO in the Windows virtual environment and the
desktop dependencies. The generation service uses port 8001 and the Core API uses 8000. The model
key is still read from `OPENAI_API_KEY`. Open TrafficForge in the workspace sidebar, enter a request
such as "Test vehicle speeds in congested traffic", and click **Generate preview** or press Enter.
The preview shows the actual generated network image, target traffic state, reference density,
planned departures, duration, warm-up and seed. Traffic mode quantifies vehicle totals and
departure rates; autonomy mode also shows L0–L5 departure shares. Total planned departures are not
the number of vehicles simultaneously on the network. Requirements to review include both the map
and demand; the user judges their suitability.

Before confirmation, only a workspace draft is stored; it is absent from the system scenario list.
Failed image loads can be retried. Changed requirements require regeneration. During generation,
the button reads **Generating…**. Missing places or service failures show a prominent reason below
the input, retain the request and provide **Try again**. A first failure keeps the simple entry;
the map and settings expand only after the first success. Only **Confirm and add scene** publishes
the draft, after which **Simulation setup** becomes available. Repeated confirmation does not
create duplicate copies.

Click the map image or **Enlarge** for a separate viewer with wheel zoom, drag-to-pan, actual size
and fit-to-window controls. Scenario settings accept direct edits to vehicle total, target density,
duration, warm-up, random seed, hourly flow per incoming lane, total hourly flow and the reference
speed used for density calculations. Autonomy mode also edits L0–L5 percentages, which must sum to
100%. Times are edited in seconds, and warm-up must be shorter than duration. **Apply all changes**
updates traffic XML, departure schedules and SUMO configuration together. Unapplied or invalid
edits block confirmation. Failed edits retain the input for retry or cancellation. Revisions reuse
the network without model calls, map downloads or simulation. Each successful revision creates a
new draft; the original draft and published scenarios are not overwritten.

Count, per-lane flow, total flow and density are four distinct demand modes; editing their driving
inputs explicitly switches modes. Density-based demand uses density × reference speed, without
the default 10% scale for an explicitly requested value. Total-hourly-flow mode floors the count
over the simulation duration; vehicle totals must be within 1–20,000. Changing duration or seed
preserves the current demand mode. Automation percentages allocate integer vehicles using the
largest-remainder method. Realized counts, hourly flow and shares shown in the preview are computed
results. Target density and congestion settings still require evaluation after a run.

`POST /scenarios/{task_id}/revise` accepts partial edits to `vehicle_count`,
`desired_density_veh_per_km_lane`, `duration_s`, `warmup_s`, `seed`,
`inflow_veh_per_hour_per_lane`, `total_flow_veh_per_hour`, `reference_speed_kmh`, `automation_mix`,
and `demand_mode` (`count/inflow/density/total_flow`). Legacy count-only requests remain supported.
Omit unchanged fields rather than sending `null`. When multiple demand-driving values are supplied,
the demand mode must be explicit. The launcher opens traffic mode by default. Once the services
are ready, open autonomy mode with
`.venv/Scripts/python.exe scripts/dev/open_trafficforge.py --mode autonomy`.

Duration can be changed and applied in the preview, rebuilding the departure plan accordingly.
Subsequent Simulation setup keeps the confirmed scenario duration, avoiding an updated end time
with an old departure schedule. **Save configuration** creates a snapshot only; **Start simulation**
uses the existing Core run workflow. Requirements unsupported by the standalone model are retained
explicitly; a successful generation does not mean they have been implemented.

Confirmed scenarios are saved as input packages under `configs/maps/nl2-<task_id>/` and discovered
dynamically without restarting. `scenario.manifest.json` records target/demand summaries and the
original intent, plan and default provenance. New manifests are version 2.0 with summaries in
`spec.trafficforge`; trial metrics are not copied. Packages are marked `scripted`, preserving the
generated routes and departure plans. Runs modify a newly created copy, leaving the source
scenario untouched. Native SUMO scenarios use 2D display; no CARLA mirror is generated yet.

The CLI is also available from the project root while the service is running:

```powershell
.venv/Scripts/python.exe trafficforge/scene_cli.py generate "Test vehicle speeds in congested traffic"
.venv/Scripts/python.exe trafficforge/scene_cli.py publish <task_id_from_generation_response>
.venv/Scripts/python.exe trafficforge/scene_cli.py list
.venv/Scripts/python.exe trafficforge/scene_cli.py show <task_id>
```

New drafts and historical tasks both require explicit `publish <task_id>` to import their inputs
and settings. `generate --publish` explicitly adds a scenario directly, and `--mode autonomy`
generates automation-level assignments; the default mode is `traffic`. The corresponding service
APIs are `GET /scenarios`, `GET /scenarios/{task_id}` and `POST /scenarios/{task_id}/publish`.
The desktop continues to read the catalog through Core `GET /api/v1/maps`; optional
`generated_scenario` contains settings only. Launching retains the existing save-configuration,
prepare and start interfaces.

To change count, call `POST /scenarios/{task_id}/revise`, for example with `{"vehicle_count": 1200}`.
The response is a new `ScenarioResult` draft with `parent_task_id` and `manual_overrides`; original
request text and quotes remain unchanged. The new draft needs its own publication confirmation.
An old task that has already been trial-run cannot be revised directly into an unrun draft.

## Scenario generation API and optional trial runs

The same entry point can generate a map, demand and optional trial results:

```json
{ "description": "Test vehicle speeds in congested traffic" }
```

`output_kind="auto"` distinguishes map-only requests from simulation objectives; explicitly choose
`map` or `scenario` to override it. Map requests retain their original behavior. For scenarios,
the model extracts the objective, traffic state, explicit numerical parameters and metrics. Code
fills defaults and creates SUMO routes/vehicles; the model does not invent trial results.

- No map specified: generate a default four-way signalized intersection through roadgen, with branches about 300 m long. Defaults are recorded.
- Real place specified: use the OSM workflow, simplifying to main roads unless more detail is requested.
- Reuse an existing map: supply `map_task_id`. A request for the "current map" without an ID returns `needs_clarification` rather than guessing the latest task. The network is copied into a new workspace without modifying the source.
- `run_simulation=false` (default) returns a `generated` draft with `metrics=null`. `publish_to_catalog=false` (default) leaves `catalog_map_id=null`; inspect images and inputs through `artifact_urls`. Confirm with `POST /scenarios/{task_id}/publish` to receive a `map_id`. Explicit `publish_to_catalog=true` preserves direct publication for older scripts. Explicit `run_simulation=true` retains one optional trial run. The desktop fixes both flags to false.
- `simulation_mode="traffic"` selects ordinary traffic. In `autonomy`, an unspecified mix defaults to L0 100% and is disclosed. A complete L0–L5 request must sum to 100%; integer counts use largest-remainder allocation and the preview displays realized shares. For example, request "L0 70%, L3 30%". Values inconsistent with the original text produce a specific reason. Ambiguous wording such as "half each" or "the rest" must be replaced by explicit percentages or remain unverified in the preview; it cannot silently count as implemented. Levels use the system's existing SUMO behavior-proxy parameters, not actual autonomous-driving algorithms.

For example, reuse a generated map with an explicit departure count and duration:

```json
{
  "description": "Use the current map to test vehicle speeds, with 500 planned departures, a 5-minute simulation and 1 minute of warm-up",
  "map_task_id": "20260101_000000_aabbcc",
  "run_simulation": false
}
```

Replace the example ID with a real map task ID. Supported explicit parameters include total
**planned departures**, hourly flow per lane, target density per kilometer per lane, simulation and
warm-up durations, seed, and speed/density/queue/travel-time metrics. Total departures and per-lane
flow cannot simultaneously drive demand. Vehicle-type shares, weather, control algorithms, custom
metrics and density/flow with unclear scope remain in `unverified_requirements` rather than being
claimed as implemented.

Defaults:

| Setting | Default and meaning |
|---|---|
| Simulation duration / warm-up | 600 s / min(120 s, 20% of duration) |
| Seed / simulation step | 42 / 0.5 s |
| Low / moderate / congested density reference | 8 / 22 / 65 vehicles/km/lane; engineering scenario references, not measurements |
| Departure rate | When count, flow and density are all unspecified, use 10% of density × reference speed (30 km/h) as a light default. Explicit density uses the full estimate; explicit count or flow follows the request |
| Vehicles | Passenger cars: length 5 m, gap 2.5 m, tau=1 s, sigma=0.5; inserted at zero speed into a suitable lane chosen by SUMO, retaining road speed limits and signal programs |
| Size limits | At most 20000 planned departures and 3600 s; oversized requests fail rather than shrinking silently |

Routes use passenger-accessible lane connections in the actual compiled network and exclude
U-turns. Boundary entries/exits are preferred, with at most 12 valid origins and up to 3 reachable
destinations per origin. If there are no boundary candidates, reachable internal routes are used.
Shortest paths are computed using average lane lengths of external roads, then the farthest
reachable destinations are preferred. A route must contain at least two roads and be at least 50 m
long. Departures are uniformly scheduled with weights proportional to incoming lane counts. The
seed controls origin sampling and route choice. This distribution is not a calibrated real-world
OD matrix. `demand` exposes the actual selection scope, route count, planned departures and
network-wide lane-kilometers. Roads not covered by selected routes still contribute to the
network-wide density denominator.

Each task saves quoted-source `intent`, complete parameters in `plan`, default provenance in
`defaults_used`, traffic demand in `demand`, and actual results in `metrics`. Configure default
demand through `SCENARIO_DEFAULT_DEMAND_SCALE`, which defaults to `0.1`; set `1` to restore the
unscaled estimate. For example, a 10-minute Beihang scenario with 18 selected incoming lanes yields
72 free-flow, 198 moderate or 585 congestion-target departures by default. Scaling only affects
newly generated requests without explicit numerical demand. The resulting traffic state still
requires simulation. Very short default scenarios schedule at least one vehicle per incoming lane
to avoid empty demand after scaling. `intent.metrics/plan.metrics` records user interests; it does
not filter output fields. The current collector always returns all metrics below:

- `mean_speed_kmh`: post-warm-up speed weighted by active vehicle count × time from SUMO summary records every 0.5 s, including junctions.
- `mean_external_density_veh_per_km_lane`: accumulated `sampledSeconds` on external passenger lanes / observation duration / all external passenger lane-kilometers. Internal junction lanes and vehicles not yet inserted are excluded.
- Queue metrics: post-warm-up time mean and maximum of stopped vehicles across the network.
- Completed trip time: arithmetic mean of `tripinfo.duration` for vehicles that entered and arrived during the whole run, including warm-up. It excludes pre-insertion waiting, unfinished vehicles and vehicles never inserted, so it does not represent full travel time for all planned departures.
- Planned departures, inserted, arrived, still active, not inserted, and vehicles waiting for insertion at the end are recorded separately. Final waiting count can differ from total uninserted count because some vehicles may not yet have reached their departure step.
- Local congestion criterion: mean speed relative to the speed limit ≤0.5 and stopped-vehicle share ≥20%, for at least 30% of post-warm-up time. Results are `observed/not_observed/no_data/invalid_run`. This is an observation from the current model trial, not a universal definition or real-world congestion prediction. Collisions/teleports invalidate the test.

`success` means the run and data generation completed and, when congestion was requested, the
criterion was observed. `needs_adjustment` means requested congestion was not observed, valid
vehicle data is missing, or a collision/teleport occurred. The service does not reduce speed
limits, change signals or hide insertion backlogs to manufacture a successful congestion result.
Other traffic states are not yet automatically judged as achieved: `target_state=not_requested`
and only observations are provided. `user_review=pending` leaves suitability for research to the
user. Target density is not an initial or sustained density controller and need not equal measured
density.

The generated workspace contains `network.net.xml`, `traffic.rou.xml`, `scenario.sumocfg`,
`metrics.add.xml`, `scenario.json`, map previews and check records. Only an explicit trial produces
`summary.xml/lane_metrics.xml/tripinfo.xml/statistics.xml`, `metrics.json` and speed/queue
`timeseries.csv`. Run `sumo -c scenario.sumocfg` in the output directory to reproduce the simulation.
A manual rerun overwrites native observation files in that directory, while Python-derived
`metrics.json` needs the collector to run again. Prefer a new API scenario to preserve earlier results.

Since v0.5, scenarios integrate with Core's native SUMO catalog and manual desktop launch. Explicit
trial runs still use the independent SUMO CLI. There is no CARLA mirror or new controller. See
SUMO's [Summary](https://sumo.dlr.de/docs/Simulation/Output/Summary.html) and
[Lane/Edge Measures](https://sumo.dlr.de/docs/Simulation/Output/Lane-_or_Edge-based_Traffic_Measures.html)
documentation for field definitions.

## V2 roadmap (deferred)

- Define a project-specific constrained RoadSpec JSON and separate natural-language interpretation from map compilation.
- Generate OpenDRIVE from RoadSpec, then derive matching SUMO and CARLA map assets as needed. Validate lanes, coordinate registration and signal bindings before co-simulation.
