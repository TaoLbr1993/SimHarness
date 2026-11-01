# Architecture and Data Flow

[Documentation](index.md) · [简体中文](../ARCHITECTURE.md)

This page describes the current two-dimensional SUMO workflow. The older [system design](../SYSTEM_DESIGN.md) also contains CARLA targets and acceptance plans; it should not be read as a list of validated features.

## Components

| Component | Responsibility |
|---|---|
| SUMO and TraCI adapter | Advance traffic simulation, expose vehicle states, and receive control requests |
| Application layer | Manage experiments and simulation steps; derive local observations from the global frame |
| REST and WebSocket API | Provide resource management, synchronous stepping, and real-time messages |
| Desktop UI | Create experiments and display live traffic and historical results |
| External Agent Harness | Assign vehicles, call models or controllers, and submit actions in batches |

## External control loop

```text
Current SUMO traffic state
       │
       ▼
Application derives each selected vehicle's local observation
       │
       ▼
External agent: observation + optional memory / skills → decision
       │
       ▼
Submit a batch of actions → advance SUMO one step → receive observations
```

The desktop UI receives global data from the backend; an agent may use `/local-step` to receive observations for selected vehicles. Both refer to the same experiment. Local observations currently filter SUMO world state by distance; they do not simulate sensors.

An externally controlled experiment waits for the next request. Slow inference therefore increases wall-clock time, but the simulation does not advance while waiting. Vehicles with no submitted action return to SUMO control. SUMO determines the actual result; an action request does not guarantee execution.

## Agent boundary

The application layer is independent of any particular model. An external program can use the [step API](external-step-api.md) directly or use the supplied [Agent Harness](agent-harness.md). Vehicle selection, model configuration, memory, and skills are managed on the agent side.

Memory is currently short-lived, in-process, and isolated per vehicle. Skills are text instructions supplied to the model. Both are disabled by default and do not change SUMO's role as the traffic-state source.

## CARLA

The repository retains code for SUMO–CARLA synchronization, regions of interest, and native-window hosting, but the current desktop run page does not embed the CARLA window. Real co-simulation and display still require integration and validation.
