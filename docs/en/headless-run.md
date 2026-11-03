# Run Without a Desktop

[简体中文](../HEADLESS_RUN.md) · [Documentation](index.md)

Run a SUMO scenario and optional vehicle agents with one command, without starting a desktop UI or a separate backend:

```bash
uv run trafficverse run --config configs/headless.example.yaml --scenario image2road
```

Install the Python dependencies and SUMO first. The command uses `sumo` on `PATH`, or tries the executable in an installed Python `sumo` package if it is not on `PATH`. `--scenario` is the ID of a discovered SUMO scenario under `configs/maps/`, not an arbitrary `.sumocfg` path. Omit it to use `map_id` from the configuration.

The command starts a temporary local backend, creates and starts the experiment, advances SUMO after each agent decision, and stops its experiment and backend on exit. No workspace or experiment ID is required. It does not connect to or stop an existing backend.

## Configuration

Copy [`configs/headless.example.yaml`](../../configs/headless.example.yaml) and edit it as needed:

```yaml
schema_version: '1.0'
map_id: image2road
max_steps: 100
step_delay_s: 0
request_timeout_s: 30
observation_radius_m: 100
bindings: []
```

`max_steps` includes the initial empty step that obtains the first observations. With empty `bindings`, no model is called, SUMO controls every vehicle, and no selected vehicle observations are recorded. If the simulation ends first, the actual step count may be lower than `max_steps`.

Bindings use the existing Agent Harness configuration:

```yaml
bindings:
  - name: api_driver
    wrapper: trafficverse.agent_harness.models.api:ApiModel
    vehicle_ids: [car_001]  # Replace with a vehicle ID present in your scenario.
    model:
      base_url: https://example.com/v1
      model_id: your-model
      api_key_env: MODEL_API_KEY
      timeout_s: 30
    memory:
      enabled: false
      max_steps: 10
    skills: []
```

Keep the key in the agent process environment; YAML specifies only its variable name:

```powershell
$env:MODEL_API_KEY = '<your-api-key>'
```

On Linux/macOS use `export MODEL_API_KEY='<your-api-key>'`. Select multiple vehicles by `vehicle_ids`, or use `automation_levels`; a vehicle matching multiple bindings raises an error. Local OpenAI-compatible services, remote APIs, Jev, and custom controllers are supported; see [Agent Harness](agent-harness.md). If configured, `skills_directory` is relative to the headless YAML file. Memory and Skills are disabled by default.

## Results

Each run creates `artifacts/headless/<run-id>/`:

- `steps.jsonl`: one JSON line for each successful step, including the submitted `request` and returned `response`. This contains actions, local observations of selected vehicles, and events. The first line is the empty step used to obtain initial observations.
- `summary.json`: experiment ID, scenario ID, final experiment status, step count, simulation time, and output paths.
- The same summary is printed to the terminal. SUMO run files and replay data are in the `artifacts/sumo/<experiment-id>/` directory identified by the summary.

Results record submitted actions and SUMO states, not raw model reasoning. A requested action is not proof of execution. Failed model calls or invalid actions are omitted; inspect logs, events, and subsequent states. On failure or interruption, existing `steps.jsonl` data remains, while `summary.json` is written only after normal completion.

Remote model calls may consume account credits. Do not put API keys in YAML or commit them to the repository.
