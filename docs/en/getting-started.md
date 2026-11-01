# Getting Started

[Documentation](index.md) · [简体中文](../GETTING_STARTED.md)

This guide starts a two-dimensional SUMO traffic simulation. To control vehicles with an external agent, continue with the [Agent Harness guide](agent-harness.md).

## 1. Prerequisites

- Python 3.10. The project currently requires `>=3.10,<3.11`.
- [uv](https://docs.astral.sh/uv/) for installing and running the Python project.
- [SUMO](https://sumo.dlr.de/docs/Downloads.php), with `sumo` available on `PATH`. The Python TraCI dependencies do not replace the SUMO executable.
- A graphical desktop capable of running PySide6.

Check your environment:

```bash
python --version
sumo --version
uv --version
```

If `sumo` cannot be found, add the SUMO `bin` directory to the terminal's `PATH` and check again.

## 2. Install

Run these commands from the directory where you want to clone the repository:

```bash
git clone https://github.com/TaoLbr1993/SimHarness.git
cd SimHarness
uv sync --frozen --extra sumo --extra ui
```

`sumo` and `ui` are Python optional dependency groups. They do not start SUMO or the desktop application.

## 3. Start the backend and desktop UI

From the project root, start the backend in one terminal:

```bash
uv run trafficverse serve --host 127.0.0.1 --port 8000
```

Keep it running and start the general traffic UI in a second terminal:

```bash
uv run trafficverse traffic-ui --api-url http://127.0.0.1:8000
```

The UI is an API client, so the backend must be running first. If you change its port, also update the UI's `--api-url` and the agent's `api_url`.

## 4. Run a scenario

1. Enter a workspace and create a simulation in the desktop UI.
2. Select a SUMO scenario, such as the included `image2road` scenario.
3. Start the simulation and observe the roads and vehicles on the run page.
4. Use simulation history to inspect, replay, or export saved formal runs.

To attach an external agent, select the UI option labeled “等待外部 Agent 控制” before starting the simulation. In this mode the simulation waits for external step requests; opening the UI alone will not advance it continuously. See [Agent Harness](agent-harness.md) for the complete workflow. The current desktop UI labels are in Chinese.

## 5. Use your own SUMO scenario

Place the scenario and its required inputs under `configs/maps/`, for example:

```text
configs/maps/my-scene/
├── my-scene.sumocfg
├── my-scene.net.xml
├── my-scene.rou.xml
└── my-scene.add.xml       # Only if referenced by the sumocfg
```

The `.sumocfg` must reference existing network, route, and additional inputs inside the allowed `configs/maps/` directory. With one `.sumocfg` in the directory, the scenario ID is the directory name. With multiple configurations, each ID uses `<directory>-<config-filename>`. The system reads the simulation time range and step length from the configuration. Runs use staged copies and do not modify the source scenario.

## Troubleshooting

- **No vehicles appear:** check that the backend is running, the scenario started, and vehicles have reached their departure times. External control also requires a client to submit steps.
- **SUMO cannot be found:** run `sumo --version` in the same terminal used to start the backend and check its `PATH`.
- **The model is not called:** the default `bindings: []` calls no model. Check that the selected vehicle IDs appear in the current frame.
- **The simulation remains running after the agent exits:** an agent attached by experiment ID does not own a desktop-created experiment. Stop it from the UI. An experiment created by the agent is stopped by the agent on exit.
