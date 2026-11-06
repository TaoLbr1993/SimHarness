# 安装与运行

[English](en/getting-started.md)

本指南介绍如何启动 SUMO 二维交通仿真。若要让外部 Agent 控制车辆，请在完成基本启动后阅读 [Agent Harness 使用说明](../src/trafficverse/agent_harness/README.md)。

## 1. 准备环境

- Python 3.10。项目当前要求 `>=3.10,<3.11`。
- [uv](https://docs.astral.sh/uv/)：用于安装和运行 Python 项目。
- [SUMO](https://sumo.dlr.de/docs/Downloads.php)：需要能够从终端执行 `sumo`。Python 的 TraCI 依赖和 SUMO 可执行程序是不同的组件。
- 可运行 PySide6 的图形桌面。

检查 Python 与 SUMO：

```bash
python --version
sumo --version
uv --version
```

如果提示找不到 `sumo`，请按 SUMO 安装方式将其 `bin` 目录加入当前终端的 `PATH`，再重新检查。

## 2. 安装项目

```bash
git clone https://github.com/TaoLbr1993/SimHarness.git
cd SimHarness
uv sync --frozen --extra sumo --extra ui
```

这些命令在项目根目录执行。`sumo` 和 `ui` 是 Python 可选依赖组，不会替你启动 SUMO 或桌面程序。

## 3. 启动后端和界面

在项目根目录打开两个终端。第一个终端运行后端：

```bash
uv run simharness serve --host 127.0.0.1 --port 8000
```

保持后端运行，在第二个终端启动普通交通界面：

```bash
uv run simharness traffic-ui --api-url http://127.0.0.1:8000
```

若更改了后端端口，界面的 `--api-url` 和 Agent 配置中的 `api_url` 也要使用相同地址。桌面界面是 API 客户端，需要先启动后端。

## 4. 运行场景

1. 在界面中进入工作区，创建仿真。
2. 在场景配置中选择 SUMO 场景，例如仓库中的 `image2road`。
3. 开始仿真，在运行页面查看道路与车辆状态。
4. 运行完成后，可从历史仿真查看正式运行记录、回放或导出结果。

要接入 Agent，应在开始前勾选“等待外部 Agent 控制”。开始后打开“智能体接入”页面配置模型和车辆，再点击“开始接入”，详见[页面接入说明](AGENT_CONNECTION.md)。此模式下仅打开界面不会使仿真自动持续推进；也可以使用 [Agent Harness 命令行](../src/trafficverse/agent_harness/README.md)。

## 5. 使用自己的 SUMO 场景

将场景及其所需文件放在 `configs/maps/` 内，例如：

```text
configs/maps/my-scene/
├── my-scene.sumocfg
├── my-scene.net.xml
├── my-scene.rou.xml
└── my-scene.add.xml       # 仅在 sumocfg 引用时需要
```

`.sumocfg` 需正确引用存在的网络、路线和其他输入文件；输入必须位于允许的 `configs/maps/` 范围内。目录里只有一个 `.sumocfg` 时场景 ID 为目录名；有多个时各场景 ID 使用 `<目录名>-<配置文件名>`。系统会按配置文件读取仿真步长和时间范围。场景运行使用副本，不修改源场景。

## 常见问题

**页面打开了但没有车辆？** 检查后端是否运行、场景是否成功启动，以及路线文件的车辆出发时间。外部控制模式还需要 Agent 或其他客户端发起步进。

**启动场景时找不到 SUMO？** 在启动后端的同一终端执行 `sumo --version`；确保该进程继承了正确的 `PATH`。

**Windows 上 SUMO 启动时仅显示 `Quitting (on unknown error)`？** 本机验证发现，SUMO 1.27.1 在 `SUMO_HOME` 指向包含中文的目录时会提前退出。使用项目虚拟环境中的 SUMO 时，把其 `bin` 目录加入 `PATH` 即可，不必额外设置 `SUMO_HOME`。若已设置了这样的路径，在启动后端的终端执行 `Remove-Item Env:SUMO_HOME -ErrorAction SilentlyContinue`，然后重启后端；不要修改场景文件。

**Agent 接入后没有调用模型？** 默认 `bindings: []` 不会调用模型。还要确认绑定的车辆 ID 实际出现在当前帧。

**Agent 结束后仿真仍在运行？** 通过实验 ID 接入桌面创建的实验时，Agent 不拥有实验生命周期；请在桌面端停止。Agent 自己创建的实验则由 Agent 在退出时停止。
