<p align="center">
  <img src="docs/assets/simharness-logo.png" alt="SimHarness Logo" width="280">
</p>

<h1 align="center">SimHarness</h1>

<p align="center">面向交通仿真的智能体 Harness，围绕仿真流程连接智能体与车辆控制。</p>

<p align="center"><a href="docs/index.md">中文文档</a> · <a href="docs/en/index.md">English Documentation</a></p>

SimHarness 基于 SUMO 提供交通仿真、车辆观测和控制接口，支持外部程序将不同类型的智能体接入同一个交通场景，并通过桌面界面查看仿真过程。项目也提供可选的 Agent Harness，用于运行大模型或自定义车辆控制器。

## 核心特性

### Agent Native

支持接入本地和远程大模型服务、Jev 以及自定义智能体。Agent Harness 提供车辆分配、模型调用和动作提交的运行流程，并支持按需启用 Memory 和驾驶风格 Skill。

### Simulation-Centric

以交通仿真为中心组织智能体与车辆的交互。外部程序可以获取车辆及其周边范围内的观测，控制部分车辆；其余车辆继续由 SUMO 控制。同一个场景可以接入大模型，也可以接入规则策略或其他非模型算法。

## 功能概览

- **SUMO 场景运行**：加载 `configs/maps/` 下的 SUMO 场景，运行交通仿真。
- **车辆观测与控制**：按距离获取邻车信息，通过接口批量提交车辆动作；支持目标速度、加速度、换道和停车请求。
- **外部智能体接入**：支持通过 API 接入，也可使用 Agent Harness 按车辆 ID 或场景中的自动驾驶等级分配控制器。
- **大模型与自定义控制器**：提供 OpenAI 兼容接口及 Jev 封装，也支持实现自定义 Python 控制器。
- **Memory 与 Skill**：可选的车辆短期记忆与驾驶风格配置，默认均不启用。
- **可视化与回放**：通过桌面界面查看交通仿真，支持保存、查看和回放正式仿真结果。

## 系统架构

```text
                     ┌───────────────────────────┐
                     │     桌面可视化界面          │
                     │  场景配置、运行状态、回放    │
                     └─────────────┬─────────────┘
                                   │ REST / WebSocket
┌──────────────────────┐           │
│   外部程序 / Agent     │           ▼
│                      │    ┌─────────────────────────┐
│  模型或自定义控制器     │◄──►│   SimHarness 应用层       │
│  可选 Memory / Skill   │    │ 实验管理、局部观测、动作   │
└──────────────────────┘    └────────────┬────────────┘
                                         │ TraCI
                                         ▼
                                ┌─────────────────┐
                                │       SUMO      │
                                │ 交通状态与仿真推进 │
                                └─────────────────┘
```

SUMO 是交通状态的来源。外部控制模式下，仿真等待外部程序提交动作，再推进下一步；本轮没有动作的车辆交由 SUMO 控制。车辆观测目前基于 SUMO 状态按平面距离筛选，不模拟传感器误差或遮挡。

## 快速开始

### 环境要求

- Python 3.10 和 [uv](https://docs.astral.sh/uv/)。
- 安装 [SUMO](https://sumo.dlr.de/docs/Downloads.php)，并确保终端的 `PATH` 中可以找到 `sumo`。
- 桌面界面需要图形环境。

### 安装

```bash
git clone https://github.com/TaoLbr1993/SimHarness.git
cd SimHarness
uv sync --frozen --extra sumo --extra ui
```

### 运行交通仿真

在第一个终端启动后端：

```bash
uv run trafficverse serve --host 127.0.0.1 --port 8000
```

在第二个终端启动普通交通界面：

```bash
uv run trafficverse traffic-ui --api-url http://127.0.0.1:8000
```

在界面中进入工作区，创建仿真并选择 SUMO 场景，然后开始运行。项目内的 `image2road` 是可用于尝试的场景之一；也可以将完整的 SUMO 场景目录放入 `configs/maps/`，通过场景中的 `.sumocfg` 加载。

## 接入智能体

下面以桌面端实验连接 Agent Harness 为例：

1. 按照快速开始启动后端和桌面界面。
2. 创建仿真时勾选“等待外部 Agent 控制”，开始仿真，并从运行页面复制实验 ID。
3. 编辑 [`configs/agent-harness.example.yaml`](configs/agent-harness.example.yaml)，填入 `experiment_id`，根据需要配置 `bindings`、模型服务和实际车辆 ID。
4. 在项目根目录启动 Agent：

   ```bash
   uv run python -m trafficverse.agent_harness --config configs/agent-harness.example.yaml
   ```

示例配置默认使用空的 `bindings`：此时 Agent 不会调用模型，车辆由 SUMO 控制，可先用于检查接入流程。配置模型前，需要准备对应模型服务；API 密钥通过环境变量提供。

Agent 按配置取得车辆的局部观测，调用控制器并批量提交动作。局部观测半径由 `observation_radius_m` 设置，默认 100 米。模型请求失败或返回无效动作时，相应动作会被省略，由 SUMO 控制车辆。

通过实验 ID 接入时，实验需要处于运行中且尚未步进。Agent 进程结束后，不会自动停止由桌面端创建的实验；可以在桌面端停止。完整配置、Memory 和 Skill 的用法见 [Agent Harness 使用说明](src/trafficverse/agent_harness/README.md)。

外部程序也可以不使用内置 Agent Harness，直接通过同步步进 API 接入。请求格式和控制语义见 [外部程序同步控制车辆](docs/EXTERNAL_STEP_API.md)。

## 项目结构

```text
SimHarness/
├── src/trafficverse/        # 仿真应用、API、SUMO 适配和 Agent Harness
├── ui/                      # 桌面界面与地图展示
├── configs/                 # 场景、地图和 Agent 配置
├── contracts/               # API 与消息契约
├── docs/                    # 项目文档
├── tests/                   # 单元、契约与集成测试
└── scripts/                 # 开发和资产处理脚本
```

## 正在开发的功能

<!-- 在此填写正在开发的功能。 -->

## 文档

- [文档入口](docs/index.md)
- [安装与运行](docs/GETTING_STARTED.md)
- [Agent Harness 使用说明](src/trafficverse/agent_harness/README.md)
- [外部程序同步控制车辆](docs/EXTERNAL_STEP_API.md)
- [Memory 与 Skill 设计和使用](docs/AGENT_MEMORY_SKILL_DESIGN.md)
- [地图资产目录与支持格式](docs/MAP_ASSET_CATALOG.md)
- [当前架构与数据流](docs/ARCHITECTURE.md)
- [系统设计与目标架构](docs/SYSTEM_DESIGN.md)
- [Agent 开发指南](docs/AGENT_DEVELOPMENT_GUIDE.md)

部分系统设计文档包含目标架构和历史方案，使用时请结合当前代码与对应功能说明。

## 贡献与许可证

欢迎通过 GitHub Issue 反馈问题或提出建议，也欢迎通过 Pull Request 参与改进。运行项目检查：

```bash
uv run ruff format --check .
uv run ruff check .
uv run mypy src tests
uv run pytest
```

仓库根目录目前尚未声明项目许可证。
