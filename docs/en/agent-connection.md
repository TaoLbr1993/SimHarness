# Connect Agents from the Desktop

[Documentation](index.md) · [简体中文](../AGENT_CONNECTION.md)

The **智能体接入** (Agent Connection) page assigns models, vehicles, memory, and skills to the current experiment. Model inference runs in the SimHarness backend; the desktop configures and monitors the session. A separate Agent CLI process is not required.

## Start a session

1. Follow [Getting Started](getting-started.md) to start the backend and general traffic UI. Restart older processes after updating to this version.
2. Select **等待外部 Agent 控制** in the simulation configuration and start the experiment. It waits for external actions while remaining in the running state.
3. Open **智能体接入** in the navigation or **接入智能体 →** on the run page. The current experiment ID is filled automatically.
4. Configure at least one control group, its model, and its vehicles. Enable memory or skills if needed.
5. Click **检查配置** (Check Configuration), then **开始接入** (Start). The check validates configuration, experiment state, and the presence of backend environment variables. It does not call a model or test service connectivity.
6. Use **查看仿真** to return to the map. The session continues in the backend and the existing simulation-frame stream updates the visualization.

The page follows the experiment currently associated with the desktop. Responses from a previous experiment cannot replace the current experiment's display.

## Models and control groups

Each group has one model and can control multiple vehicles. Different groups can use different models in the same experiment. Each round collects the groups' actions and advances SUMO once.

| Setting | Behavior |
| --- | --- |
| Configuration source | Enter settings or load an API preset from **资产中心 → 智能体配置**. Editing the session does not change that asset. |
| Model type | Remote OpenAI-compatible API, local OpenAI-compatible service, or Jev |
| Service URL and model ID | Use the existing wrapper conventions. API/local URLs include any required version path but exclude `/chat/completions`. |
| Key environment variable | Enter a name such as `DEEPSEEK_API_KEY`, not the key. Optional for unauthenticated local services. |
| Model timeout | 1–300 seconds per model request |
| Jev acceleration | Acceleration magnitude used to translate Jev's discrete actions |

Credentials are read from the **backend process environment**. Setting them only in the desktop terminal has no effect. Restart the backend after changing its environment. Only variable names appear in session configuration.

Local models must already be served over HTTP; the page does not load model weights. Custom Python controllers remain available through the [Agent Harness CLI](agent-harness.md).

## Vehicles and observations

- Select vehicles in the current frame or enter comma-separated IDs. Future departures can be assigned in advance.
- **全部车辆（包括后续进入）** selects all current and future vehicles. This requires a single group to prevent overlapping assignments.
- A vehicle can belong to only one group. Unassigned vehicles and vehicles without valid returned actions use SUMO control.
- The page uses vehicle IDs, not L0–L5 grouping, and the list comes from the real current frame.

At frame zero there may be no snapshot yet. Enter known IDs or select all vehicles; the session advances one frame with empty actions to obtain initial observations. That frame counts toward the budget. Attaching later uses the current frame directly without an extra initial step.

The observation radius applies to the entire session. Each model receives ego-centered observations containing vehicles within that radius, using the existing local-observation mapping.

## Memory and skills

Memory and Skill are separate panels at the same level as the model panel, with larger icons and their own status labels. Both are disabled by default.

- **Memory:** retain the most recent 1–1000 rounds per vehicle, including observations, requested actions, and the following frame. Configure the limit per group.
- **Skill:** cautious, **balanced**, or reckless driving. Balanced uses the existing `human_normal` skill. Usually select one driving style per group to avoid conflicting instructions.

Enabled memory and skills are added to each vehicle's model context every round. Context retrieval failures omit that part; model failures or invalid actions fall back to SUMO for the affected vehicles. See [Memory and Skills](memory-and-skills.md).

## Per-round inputs and outputs

Starting a session opens the **逐轮输入 / 输出** (Round Inputs / Outputs) tab automatically. You can return to the configuration tab at any time.
The default **信息卡片** (Information Cards) view has a round/group list and cards for observations, memory, skills, vehicle actions, model responses, and validated group actions. Select a vehicle to inspect its context. The original table and JSON tabs remain under **原始详情** (Raw Details). Switching views retains the selected record and does not invoke a model or change simulation state.

Cards summarize existing responses without generating new explanations. Inference results remain distinct from submission status; the SUMO-control count appears only after the round is submitted.

Each group has one record per round, with the input frame, model, vehicle count, duration, valid-action count, and SUMO fallback count.
Multiple groups at the same frame appear separately. The initial empty-action step obtains observations without calling a model and has no decision record.

Select a record to inspect:

- **Input:** local observations and the assembled per-vehicle memory and skills. Context appears before potentially long observation data.
- **Model request:** the request body, including prompts. Multiple Jev requests within a round are shown together.
- **Raw output:** the model service response, including malformed responses that caused fallback.
- **Validated actions:** actions that passed model-output validation. An empty array means no valid external actions for this group.

Inputs appear during inference; outputs update when the call finishes. Inference and submission status are separate: a returned response has not necessarily advanced the simulation. Submitted means the batch was handed to the simulation, not that every action was accepted by the engine. Pending results discarded by pause or stop are labeled accordingly. Simulation events remain the source for execution errors and outcomes.

The view follows the latest record by default and shows up to 20 records per page. Selecting a row stops following so you can inspect it or browse older pages.
Records are saved under `artifacts/agent-sessions/<experiment_id>/<session_id>/`, independently of UI polling. Each text field retains up to 128,000 characters, with an explicit truncation notice. Authorization headers are omitted, and the current call's key is redacted from request and response text. Storage failures show a warning without stopping simulation.

This view covers backend-managed desktop sessions, not independent Agent CLI calls. Pause/resume retains records; starting after ending creates a new record set. The UI queries only the current backend session. Backend restarts do not restore sessions, but saved files remain on disk.

Card input data is stored separately in the optional `input_preview` field, so long raw-text truncation does not prevent the overview from being displayed. Previews cover up to 100 vehicles, each with its nearest three neighbors, two recent memory entries, and three skills (up to 1,200 characters per skill). The page indicates omitted content. Older records without this field remain accessible in Raw Details. These display limits do not change the full input sent to the model.

## Pause, resume, and stop

| Action | Result |
| --- | --- |
| Start | Observe the current frame, call models, and submit a batch |
| Pause | Discard pending actions when the current model call returns, then pause |
| Resume | Continue from the stopped frame with a fresh frame budget |
| Reach the frame limit | Pause automatically |
| End session | Wait for the current call, discard its pending actions, and release control |

Pausing or ending a session does not stop the experiment or enable automatic stepping: the simulation still waits for external actions. Stop the experiment from the run page when finished. A step already submitted is allowed to complete.

A paused session retains exclusive control; competing `/step` and `/local-step` requests receive 409. End the session before using another external controller. Resuming with unchanged configuration preserves memory; changing configuration or starting after ending creates fresh memory.

The extra interval is added after inference and stepping. Groups currently run sequentially, so round latency includes each group's inference time. An in-flight call is bounded by its model timeout; pause and stop do not block the UI.

Switching pages or closing the desktop leaves the backend session running until its frame limit, experiment completion, or a session error. Backend shutdown cleans up sessions and prevents pending inference results from advancing the simulation. Session configuration and memory are currently process-local and do not survive backend restarts.

## REST resources

Prefix: `/api/v1/experiments/{experiment_id}/agent-session`.

| Method and suffix | Purpose |
| --- | --- |
| `GET` | Read state, configuration, current vehicles, and sequence |
| `POST /check` | Validate configuration without model inference |
| `POST /start` | Start or resume asynchronously |
| `POST /pause` | Request pause |
| `POST /stop` | Request session termination |
| `GET /decisions` | Paginated summaries: `before_id` cursor and `limit` (default 20, maximum 50) |
| `GET /decisions/{record_id}?session_id=...` | Session-specific detail, preventing record IDs from being confused across sessions |

Check and start accept the same typed configuration. Pause and stop need no configuration. See the generated [OpenAPI contract](../../contracts/openapi.yaml) for fields. Pause a running session before editing its configuration.
