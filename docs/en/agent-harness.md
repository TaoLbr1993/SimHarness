# Agent Harness

[Documentation](index.md) · [简体中文](../../src/trafficverse/agent_harness/README.md)

Agent Harness runs as a separate external program. It connects to a SimHarness experiment, selects vehicles, passes local observations to models or custom controllers, and submits their actions as a batch. Vehicles without actions are controlled by SUMO.

The [Agent Connection page](agent-connection.md) can also start a backend-hosted session using the same model, memory, and skill components without a separate CLI process. This guide focuses on the standalone CLI.

You can also use the [external control API](external-step-api.md) directly without this module.

## Control loop

```text
Connect to experiment → advance with empty actions for the first observations
→ select vehicles and read local observations
→ optionally load memory and skills
→ call models or custom controllers
→ submit a batch of actions → receive the next observations → repeat
```

External stepping waits for each request. Models for different bindings are called sequentially, so inference latency affects wall-clock speed. SUMO determines the actual outcome of each request.

## Attach to a desktop experiment

1. Start the backend and desktop UI as described in [Getting Started](getting-started.md).
2. Create a simulation, select “等待外部 Agent 控制” (wait for external agent control), and start it.
3. Copy its experiment ID from the run page and set `experiment_id` in [`configs/agent-harness.example.yaml`](../../configs/agent-harness.example.yaml).
4. From the project root, start the agent:

   ```bash
   uv run python -m trafficverse.agent_harness --config configs/agent-harness.example.yaml
   ```

The sample has `bindings: []`: no model is called and SUMO controls all vehicles. Use it to check the connection. The agent first advances one step with empty actions to obtain observations; this counts toward `max_steps`.

An attached experiment must have external stepping enabled, be running, and have not yet advanced. Create a new experiment if it has already stepped. The agent does not stop a desktop-created experiment on exit; stop it from the UI.

Alternatively, omit `experiment_id` and provide `workspace_id`, `scenario_id`, and `map_id` to let the agent create the experiment. The agent stops an experiment it created when it exits.

## Base configuration

```yaml
api_url: http://127.0.0.1:8000
experiment_id: 00000000-0000-0000-0000-000000000000 # Replace with the actual ID
max_steps: 100
step_delay_s: 0.2
request_timeout_s: 30
observation_radius_m: 100
bindings: []
```

| Field | Purpose | Code default |
|---|---|---|
| `api_url` | Backend address without `/api/v1` | `http://127.0.0.1:8000` |
| `experiment_id` | Existing experiment ID; omit to create a run | Empty |
| `workspace_id`, `scenario_id`, `map_id` | Required to create a run | Empty |
| `configuration_id` | Optional saved configuration ID for a new run | Empty |
| `max_steps` | Maximum steps, including the initial empty step | `100` |
| `step_delay_s` | Extra wall-clock delay per step; `0` adds no delay | `0` |
| `request_timeout_s` | Simulation API timeout in seconds | `30` |
| `observation_radius_m` | Positive XY observation radius in meters | `100` |
| `skills_directory` | Custom Skill directory; relative to the config file | Empty, use bundled directory |
| `bindings` | Agent-to-vehicle assignments | Empty |

The sample configuration explicitly sets `step_delay_s: 0.2`.

## Select vehicles

Each binding has a unique name, a controller, and at least one selector. Set `vehicle_ids`, `automation_levels`, or `all_vehicles: true` for all current and future vehicles. Selectors are combined as a union; `all_vehicles` defaults to false. Automation levels come from the simulation snapshot; verify labels in custom scenarios.

```yaml
bindings:
  - name: driver_a
    wrapper: my_agents:RuleDriver
    vehicle_ids: [car_001, car_002] # Replace with IDs that actually appear
```

A binding can control multiple vehicles. A model is not called for a vehicle before it appears in the frame. If a vehicle matches multiple bindings, the manager reports an error and stops rather than silently assigning ownership.

Each observation has `ego`, `radius_m`, and `nearby_vehicles`. Neighbors are selected by inclusive XY Euclidean distance and sorted by distance and ID. This is not a visibility, front/rear, or lane-change safety model.

## Local and remote chat models

Both wrappers use an OpenAI-compatible Chat Completions endpoint. For a local service, replace the URL, model ID, and vehicle ID:

```yaml
bindings:
  - name: local_driver
    wrapper: trafficverse.agent_harness.models.local:LocalModel
    vehicle_ids: [car_001]
    model:
      base_url: http://127.0.0.1:8001/v1
      model_id: your-local-model
      timeout_s: 30
```

Start the model service separately. `base_url` includes the provider's version path but excludes `/chat/completions`. For a remote service, use `trafficverse.agent_harness.models.api:ApiModel`:

```yaml
bindings:
  - name: api_driver
    wrapper: trafficverse.agent_harness.models.api:ApiModel
    vehicle_ids: [car_001]
    model:
      base_url: https://example.com/v1
      model_id: your-model
      api_key_env: MODEL_API_KEY
      timeout_s: 30
```

Put the real key in the agent process environment; `api_key_env` contains only the variable name:

```powershell
$env:MODEL_API_KEY = '<your-api-key>'
```

On Linux/macOS use `export MODEL_API_KEY='<your-api-key>'`. Omit `api_key_env` for a local service that needs no key. `model.timeout_s` controls model requests and is separate from `request_timeout_s` for simulation requests.

The response should be JSON such as `{"actions":[{"vehicle_id":"car_001","desired_speed_mps":8.0}]}`. A complete Markdown code fence around the JSON is also accepted. Failed or timed-out requests and invalid actions result in the affected actions being omitted. Unassigned or duplicate vehicle actions are rejected. Validation does not prove a strategy is safe; SUMO determines actual execution.

## Jev

Jev uses OpenRouter's Decisions API rather than Chat Completions:

```yaml
bindings:
  - name: jev_driver
    wrapper: trafficverse.agent_harness.models.jev:JevModel
    vehicle_ids: [car_001]
    model:
      base_url: https://openrouter.ai/api
      model_id: typesafe/jev-1.14
      api_key_env: OPENROUTER_JEV_SK
      timeout_s: 30
      jev_acceleration_mps2: 1.0
```

Set `OPENROUTER_JEV_SK` in the agent environment. The adapter offers SUMO control, acceleration, deceleration, left/right lane changes, and stop. Acceleration magnitude is configured with `jev_acceleration_mps2`. Remote requests may consume account credits.

## Memory and Skills

Both are disabled or unconfigured by default. Add these fields to a binding to enable them:

```yaml
    memory:
      enabled: true
      max_steps: 10
    skills: [human_cautious]
```

Memory is isolated by experiment and vehicle and cleared when the process exits. Built-in skills are `human_cautious`, `human_normal`, and `human_reckless`; they are model instructions, not guaranteed behavior. See [Memory and Skills](memory-and-skills.md) for custom skills and extension interfaces.

## Custom controller

Create an importable Python class implementing `decide(observations)` and returning a tuple of `VehicleControlRequest` objects:

```python
from trafficverse.api.models import VehicleControlRequest
from trafficverse.domain.models.observation import VehicleObservation


class RuleDriver:
    def decide(
        self, observations: tuple[VehicleObservation, ...]
    ) -> tuple[VehicleControlRequest, ...]:
        return tuple(
            VehicleControlRequest(vehicle_id=obs.ego.vehicle_id, desired_speed_mps=8.0)
            for obs in observations
        )
```

Save it as an importable `my_agents.py` and configure `wrapper: my_agents:RuleDriver`. Without `model`, the class is constructed with no arguments; with `model`, it receives a `ModelConfig` argument.

To consume memory or skills, also implement `decide_with_context(inputs)` as described in [Memory and Skills](memory-and-skills.md). A controller can only return actions for its assigned vehicles, with no duplicate IDs.

## Troubleshooting

- **Simulation waits:** check external control, the agent process, and `api_url`.
- **No model requests:** check bindings, IDs, and whether the selected vehicles have entered the current frame.
- **Cannot attach:** verify the experiment is running, externally stepped, and has not advanced yet.
- **No visible action:** inspect logs and events; the request may be invalid, target an absent vehicle, or be rejected by SUMO. Lane changes are not guaranteed.
- **Timeout:** distinguish model timeout from simulation API timeout. Do not blindly retry a step with a lost response; see [sequence and failures](external-step-api.md#sequence-and-failures).
