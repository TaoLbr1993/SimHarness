# External Vehicle Control API

[Documentation](index.md) · [简体中文](../EXTERNAL_STEP_API.md)

Use these HTTP APIs to connect your own algorithm to an experiment. To reuse vehicle assignment and model adapters, see [Agent Harness](agent-harness.md).

## Overview

| Endpoint | Purpose | Response |
|---|---|---|
| `POST /api/v1/experiments/{id}/step` | Submit actions and advance one step | Global simulation frame |
| `POST /api/v1/experiments/{id}/local-step` | Submit actions and advance one step | Local observations for selected vehicles and events |

Both require an experiment with `external_stepping` enabled and status `RUNNING`. They advance the same experiment: **every successful `/local-step` request advances it by one step**, so the endpoint is not a read-only observation API. Global WebSocket messages are available for observing and displaying the experiment.

Actions are based on the preceding frame; `expected_sequence` must match that frame. The external client selects vehicles, produces actions, and decides when to request the next step. The simulation waits while the client is deciding.

## Local observation step

```json
{
  "expected_sequence": 0,
  "actions": [],
  "radius_m": 100.0,
  "vehicle_ids": ["car_001"],
  "automation_levels": [],
  "all_vehicles": false
}
```

`vehicle_ids` and `automation_levels` are combined as a union. `all_vehicles: true` selects all current vehicles. Selecting none yields an empty observation list but still advances the simulation. A vehicle absent from the resulting frame has no observation.

The response contains `experiment_id`, `sequence`, `simulation_time_ms`, `radius_m`, `observations`, and `events`. Each observation has `ego`, `radius_m`, and `nearby_vehicles`; each nearby item has a `vehicle` and `distance_m`. Vehicle states include fields such as `vehicle_id`, `position`, `speed_mps`, `acceleration_mps2`, `heading_rad`, and `lane_id`.

Distances use SUMO XY coordinates in meters; the radius is inclusive, excludes the ego vehicle, and must be positive and finite. Neighbors are sorted by distance and vehicle ID. This filter does not model visibility, front/rear relationships, or lane-change safety.

### Python example

Create and start a fresh, unstepped external-control experiment in the desktop UI. Replace the experiment and vehicle IDs below. This example submits empty actions, so it checks the connection while SUMO controls every vehicle. No third-party HTTP library is required.

```python
import json
from urllib.request import Request, urlopen

base_url = "http://127.0.0.1:8000"
experiment_id = "replace-with-experiment-id"
vehicle_id = "replace-with-actual-vehicle-id"
url = f"{base_url}/api/v1/experiments/{experiment_id}/local-step"
sequence = 0

for _ in range(10):
    payload = {
        "expected_sequence": sequence,
        "actions": [],
        "radius_m": 100.0,
        "vehicle_ids": [vehicle_id],
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        frame = json.load(response)
    sequence = frame["sequence"]
    print("sequence:", sequence, "observations:", len(frame["observations"]))
```

The observation count can be zero before the selected vehicle enters the simulation. To control it, populate `actions` for the next request using the preceding observation. Stop the experiment from the UI after the example.

## Global frame step

Create an experiment with `external_stepping: true`, then prepare and start it. Submit an initial `POST /api/v1/experiments/{id}/step` request with `{"expected_sequence": 0, "actions": []}`. It advances one SUMO step and returns the first traffic frame. Use its `traffic.sequence` and `traffic.vehicles` to construct the next request.

```json
{
  "expected_sequence": 1,
  "actions": [
    {"vehicle_id": "car_001", "desired_speed_mps": 10.0},
    {"vehicle_id": "car_002", "desired_acceleration_mps2": -1.0},
    {"vehicle_id": "car_003", "lane_change": "LEFT"}
  ]
}
```

In external stepping mode, the built-in mixed-automation rule controller and TrafficVerse collision-vehicle freezing do not run. Vehicles without actions use SUMO's native models. SUMO continues to determine actual motion and apply its safety constraints.

## Action fields and limits

| Field | Meaning | Constraints |
|---|---|---|
| `vehicle_id` | Vehicle to control | Required; at most once per batch |
| `desired_speed_mps` | Target speed in m/s | Nonnegative; cannot be combined with acceleration or stop |
| `desired_acceleration_mps2` | Target acceleration in m/s² | Cannot be combined with speed or stop |
| `lane_change` | Relative lane change | `LEFT`, `RIGHT`, or default `NONE`; may not be executed |
| `stop_requested` | Request a stop | When `true`, cannot be combined with speed or acceleration |

Each action requires at least one control intent. `takeover_requested` is not supported as a synchronous step action. A lane-change request may accompany speed or acceleration. Acceleration and lane-change requests last one simulation step; lane changes are not guaranteed to complete.

When a vehicle previously had a speed or stop override and the next batch does not contain such an action for it, the override is explicitly released through TraCI. If an action targets a vehicle absent from the previous frame or SUMO rejects it, other valid vehicle actions may still execute; `events` includes `vehicle.control.rejected` with the relevant vehicle and reason. An action can be partially applied and cannot be transactionally rolled back. If a control override cannot be safely released, the experiment fails.

## Sequence and failures

`expected_sequence` starts at 0 and must match the latest returned frame sequence (`traffic.sequence` for `/step`, `sequence` for `/local-step`). Invalid requests and stale or duplicate sequences do not advance the simulation. If a step response is lost, do not blindly retry: use the existing WebSocket `world.snapshot.request` to inspect the latest global frame and sequence before deciding how to proceed.

These APIs do not invoke models or assign controllers. The external client is responsible for mapping vehicles to agents.
