# SimHarness Documentation

[简体中文](../index.md) | English

SimHarness is an agent harness for traffic simulation. It uses SUMO to advance traffic, provides vehicle observations and batch control APIs, and offers a desktop visualization. External programs can control vehicles with their own models or algorithms.

## Guides

For a single-command run, see [Run Without a Desktop](headless-run.md).

For visual configuration, see [Connect Agents from the Desktop](agent-connection.md): assign models, vehicles, memory, and skills, then start or pause a session.

1. [Getting Started](getting-started.md): install dependencies, start the backend and desktop UI, and run a SUMO scenario.
2. [Agent Harness](agent-harness.md): connect to an experiment, select vehicles, and configure a local model, API, Jev, or custom controller.
3. [External Control API](external-step-api.md): use synchronous step APIs directly from your own program.
4. [Memory and Skills](memory-and-skills.md): configure per-vehicle memory and driving-style skills.
5. [Architecture](architecture.md): understand the data flow between simulation, agents, API, and UI.

The current main workflow is two-dimensional SUMO simulation. The repository contains SUMO–CARLA integration code and designs, but the current desktop run page does not embed a CARLA window. Real CARLA co-simulation still requires integration and validation.

The [Chinese documentation](../index.md) also links to historical design documents. In particular, the earlier asynchronous LLM control proposal does not describe the current synchronous Agent Harness.
