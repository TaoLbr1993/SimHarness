# Agent Memory and Driving Skills

[Documentation](index.md) · [简体中文](../AGENT_MEMORY_SKILL_DESIGN.md)

Memory records a vehicle's recent decisions and observed next states. A Skill is text supplied to a model to describe a driving style or behavior preference. Both are optional agent-side context and are disabled or unconfigured by default. Neither directly controls SUMO.

## Configuration

Memory and Skills are configured per binding:

```yaml
bindings:
  - name: driver
    wrapper: trafficverse.agent_harness.models.api:ApiModel
    vehicle_ids: [car_001, car_002]
    model:
      base_url: https://example.com/v1
      model_id: your-model
      api_key_env: MODEL_API_KEY
    memory:
      enabled: true
      max_steps: 10
    skills:
      - human_reckless
```

Each binding maintains separate memory keyed by experiment and vehicle. `max_steps` is the maximum number of records per vehicle, from 1 to 1000, and defaults to 10. The memory is not persistent or shared across experiments.

## Memory records and lifecycle

A record is written after the next simulation step succeeds:

| Field | Meaning |
|---|---|
| `observation` | The vehicle's local observation at decision time |
| `requested_action` | The action submitted for that vehicle, or empty if none |
| `next_state` | The vehicle's actual next state, or empty if unavailable |
| `next_sequence` | The next frame's sequence |
| `events` | Events associated with that vehicle, such as control rejection |

Each vehicle has a bounded queue. The first decision normally has no history. When a vehicle is absent from the next observation frame, its records are removed; all records are cleared when the process exits. `max_steps` counts records, not seconds.

Requested actions are stored separately from observed outcomes. The absence of a rejection does not prove a lane change completed. Do not infer an outcome when the next state is unavailable.

## Built-in and custom Skills

The built-in Skills are `human_cautious`, `human_normal`, and `human_reckless`. They describe preferences for the model; they do not call TraCI, advance the simulation, or guarantee a particular driving style. SUMO still determines the actual outcome.

To use a custom Skill, configure a directory and name:

```yaml
skills_directory: my-skills
bindings:
  - name: driver
    wrapper: trafficverse.agent_harness.models.api:ApiModel
    vehicle_ids: [car_001]
    skills: [calm_driver]
    model:
      base_url: https://example.com/v1
      model_id: your-model
      api_key_env: MODEL_API_KEY
```

For a config at `configs/agent.yaml`, this looks for `configs/my-skills/calm_driver.md`. A UTF-8 file might contain:

```markdown
Drive smoothly. Anticipate deceleration and remain cautious when information is insufficient.
```

Names must start with a letter or digit and can contain letters, digits, underscores, and hyphens, up to 64 characters. Files must be nonempty and at most 16 KiB. When a custom directory is configured, the bundled directory is not searched; copy any built-in Skills you also need into the custom directory.

## Extension interfaces

The memory and Skill providers expose these protocols:

```python
class MemoryStore(Protocol):
    def read(self, experiment_id: UUID, vehicle_id: str) -> tuple[MemoryRecord, ...]: ...
    def append(self, experiment_id: UUID, vehicle_id: str, record: MemoryRecord) -> None: ...
    def retain(self, experiment_id: UUID, vehicle_ids: frozenset[str]) -> None: ...


class SkillProvider(Protocol):
    def load(self, name: str) -> SkillContent: ...
```

Inputs are organized per vehicle as an `observation`, optional `memory`, and loaded `skills`. Each round reads context, calls the controller, validates and collects actions, advances one step, and updates memory from the returned frame.

A custom controller must still implement `decide(observations)`. To receive context, add this method to the class:

```python
from trafficverse.agent_harness.context import AgentInput
from trafficverse.api.models import VehicleControlRequest


def decide_with_context(
    self, inputs: tuple[AgentInput, ...]
) -> tuple[VehicleControlRequest, ...]:
    for item in inputs:
        vehicle_id = item.observation.ego.vehicle_id
        memory = item.memory  # May be None if disabled or reading failed
        skills = item.skills
        # Choose an action using this vehicle's observation and context.
    return ()  # Example: submit no actions; SUMO controls the vehicles.
```

The built-in local, API, and Jev wrappers support contextual inputs. When memory is disabled and no Skills are configured, the manager uses `decide(observations)`. If context is configured but a custom controller lacks `decide_with_context`, the manager logs a warning and calls the old interface without context.

## Failures and limits

- A disabled or failed memory read gives the current decision no memory; a failed Skill load skips that Skill and logs a warning.
- Memory write failures do not undo or repeat an already completed step.
- Skills are supplied in configuration order. The model is instructed to prefer the first in a conflict, but this is not guaranteed.
- The simulation waits during synchronous inference. Failed model requests or invalid actions result in no submitted action for the affected vehicle, allowing SUMO control.
- Memory and model inferences are not the authoritative SUMO state.
