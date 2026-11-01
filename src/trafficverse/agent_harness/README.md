# Agent Harness 使用说明

[English](../../../docs/en/agent-harness.md)

Agent Harness 是独立运行的外部程序。它连接 SimHarness 实验，按配置选择车辆，把局部观测交给模型或自定义控制器，然后将动作汇总为一个批次提交。没有动作的车辆由 SUMO 控制。

如果只想通过 HTTP API 接入自己的程序，不需要使用此模块，参见 [外部程序同步控制车辆](../../../docs/EXTERNAL_STEP_API.md)。

## 工作流程

```text
连接实验 → 空动作推进并获取首帧
        → 为选中的车辆读取局部观测
        → 可选读取 Memory 和 Skill
        → 调用模型或自定义控制器
        → 汇总并提交车辆动作 → 取得下一帧 → 循环
```

外部控制模式中，仿真在每一步等待外部请求。Agent 内不同 binding 的模型按顺序调用，所以模型延迟会影响仿真的现实运行速度。SUMO 仍决定动作的实际效果。

## 连接桌面端实验

1. 按照[安装与运行](../../../docs/GETTING_STARTED.md)启动后端和普通交通界面。
2. 创建仿真，在配置中勾选“等待外部 Agent 控制”并开始运行。
3. 从运行页面复制实验 ID，填入 [`configs/agent-harness.example.yaml`](../../../configs/agent-harness.example.yaml) 的 `experiment_id`。
4. 在项目根目录启动 Agent：

   ```bash
   uv run python -m trafficverse.agent_harness --config configs/agent-harness.example.yaml
   ```

示例配置的 `bindings: []` 不会调用模型，所有车辆由 SUMO 控制，适合先确认接口和界面联通。Agent 会先发送空动作并推进一步获取初始观测；这一步也计入 `max_steps`。

按 ID 接入的实验必须开启外部控制、处于运行中，且尚未步进。如果已执行过步进，需要重新创建实验。Agent 不会在退出时停止桌面端创建的实验；请在桌面端停止。

也可以不填写 `experiment_id`，改为配置 `workspace_id`、`scenario_id` 和 `map_id`，由 Agent 创建实验。此时 Agent 在退出时负责停止自己创建的实验。

## 基础配置

示例文件包含完整注释；以下是连接已有实验的最小形式：

```yaml
api_url: http://127.0.0.1:8000
experiment_id: 00000000-0000-0000-0000-000000000000 # 替换为实际实验 ID
max_steps: 100
step_delay_s: 0.2
request_timeout_s: 30
observation_radius_m: 100
bindings: []
```

| 配置 | 作用 | 默认值 |
|---|---|---|
| `api_url` | SimHarness 后端地址，不含 `/api/v1` | `http://127.0.0.1:8000` |
| `experiment_id` | 桌面端已有实验 ID；不填时由 Agent 创建实验 | 空 |
| `workspace_id`、`scenario_id`、`map_id` | Agent 自建实验时使用 | 空 |
| `configuration_id` | 自建实验使用的可选配置 ID | 空 |
| `max_steps` | 最大步数，包含获取首帧的空动作步 | `100` |
| `step_delay_s` | 每轮额外等待的现实时间，便于观察；`0` 不额外等待 | `0` |
| `request_timeout_s` | 仿真 API 请求超时，单位为秒 | `30` |
| `observation_radius_m` | 局部观测的平面半径，单位为米，必须大于 0 | `100` |
| `skills_directory` | 自定义 Skill 目录，相对路径基于配置文件所在目录 | 空，使用内置目录 |
| `bindings` | Agent 与车辆的绑定列表 | 空 |

示例 YAML 对 `step_delay_s` 显式设置为 `0.2`，不同于代码默认值。

## 选择车辆

每个 binding 有独立名称、控制器和车辆选择条件。`vehicle_ids` 和 `automation_levels` 至少填写一个；同时填写时匹配任一条件即可。等级以仿真快照中的 `automation_level` 为准，自定义场景应先确认该标注符合预期。

```yaml
bindings:
  - name: driver_a
    wrapper: my_agents:RuleDriver
    vehicle_ids: [car_001, car_002] # 替换为实际出现的车辆 ID
```

一个 binding 可以控制多辆车；车辆尚未进入仿真时不会为其调用模型。一辆车同时匹配多个 binding 会报错并停止，避免控制权产生歧义。

每个局部观测包含 `ego`、`radius_m` 和 `nearby_vehicles`。邻车按平面 XY 欧氏距离筛选，包含边界，按距离和车辆 ID 排序；它不表示可见性、前后车关系或换道安全。

## 配置本地模型或远程 API

本地和远程封装都调用 OpenAI 兼容的 Chat Completions 接口。以下是连接本地服务的示例，将它放在基础配置的 `bindings` 下，替换实际模型地址、模型名和车辆 ID：

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

`base_url` 应包含服务所需的版本路径，不包含 `/chat/completions`；模型服务需要由用户自行启动。远程 API 使用相同的模型配置，把 `wrapper` 改为 `trafficverse.agent_harness.models.api:ApiModel`，并按服务实际地址和模型名填写，例如：

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

将真实密钥设置在运行 Agent 的终端环境中，`api_key_env` 只写变量名称：

```powershell
$env:MODEL_API_KEY = '<your-api-key>'
```

Linux/macOS 可使用 `export MODEL_API_KEY='<your-api-key>'`。不需要密钥的本地服务可以省略 `api_key_env`。`model.timeout_s` 是模型请求超时，与仿真 API 的 `request_timeout_s` 分别配置。

模型应返回 JSON，例如 `{"actions":[{"vehicle_id":"car_001","desired_speed_mps":8.0}]}`。也接受完整包裹在 Markdown 代码块中的 JSON。请求失败、超时、格式或动作无效时，相应车辆的动作会被省略；越权或重复的车辆动作不会执行。校验不代表策略安全，动作的实际执行仍由 SUMO 决定。

## 配置 Jev

Jev 使用 OpenRouter Decisions API，与 Chat Completions 接口不同：

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

在 Agent 进程的环境中设置 `OPENROUTER_JEV_SK`。当前封装提供交回 SUMO、加速、减速、左换道、右换道和停车选项；加减速幅度由 `jev_acceleration_mps2` 配置。调用远程服务可能消耗账户额度。

## 启用 Memory 和 Skill

两者均默认关闭或未配置。可在一个 binding 下添加：

```yaml
    memory:
      enabled: true
      max_steps: 10
    skills: [human_cautious]
```

Memory 按实验和车辆隔离，保留近期记录，进程结束后清空。内置 Skill 包括 `human_cautious`、`human_normal` 和 `human_reckless`；它们是提供给模型的文本指引，不保证模型一定遵循。自定义 Skill、失效处理和接口说明见 [Memory 与 Skill](../../../docs/AGENT_MEMORY_SKILL_DESIGN.md)。

## 接入自定义控制器

提供可导入的 Python 类，至少实现 `decide(observations)`，返回 `VehicleControlRequest` 元组。例如：

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

保存为可由 Python 导入的 `my_agents.py` 后，使用 `wrapper: my_agents:RuleDriver`。未配置 `model` 时类以无参数方式构造；配置了 `model` 时会将 `ModelConfig` 作为构造参数传入。

如需读取 Memory 或 Skill，还需实现 `decide_with_context(inputs)`。旧接口依然可用，但不会收到这些上下文；参见 [接口说明](../../../docs/AGENT_MEMORY_SKILL_DESIGN.md)。控制器只能为本 binding 分配的车辆返回动作，同一车辆只能出现一次。

## 故障排查

- **仿真一直等待**：检查实验是否开启外部控制、Agent 是否在运行，以及 `api_url` 是否指向同一后端。
- **模型没有请求**：检查 `bindings` 是否为空、车辆 ID 是否正确、车辆是否已进入当前仿真帧。
- **接入已有实验失败**：检查实验状态与 `latest_sequence`；只能接入尚未步进的外部控制实验。
- **模型动作没有效果**：检查 Agent 日志及响应事件；请求可能无效、车辆不存在或被 SUMO 拒绝，换道也不保证完成。
- **超时**：区分模型超时和仿真 API 超时。步进响应丢失时不要盲目重试，避免重复推进；参见 [接口文档](../../../docs/EXTERNAL_STEP_API.md)。
