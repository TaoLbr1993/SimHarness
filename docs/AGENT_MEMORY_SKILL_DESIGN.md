# Agent Memory 与驾驶风格 Skill

[English](en/memory-and-skills.md)

> 状态：首版已实现；短期记忆仅保存在 Agent 进程内。两项功能默认均不启用。

Memory 是车辆过去几次决策和实际下一帧状态的记录。Skill 是提供给模型的驾驶风格或行为原则文本，例如更谨慎地驾驶。它们均属于 Agent 层的可选上下文，不直接控制 SUMO。

## 职责和配置

Agent 根据每辆车的局部观测读取记忆、加载驾驶风格 Skill，并组合成模型输入。
Manager 负责选择车辆、收集模型动作并批量提交，SUMO 仍是车辆状态真值。
配置按 Agent binding 生效，例如：

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

Memory 默认关闭，启用后以 binding、`experiment_id` 和 `vehicle_id` 隔离，默认每辆车保留
最近 10 条记录；`max_steps` 可配置为 1–1000。车辆不再出现在下一帧观测中时清理对应记忆，
Agent 进程结束时全部清理；首版不持久化，也不跨实验共享。
Skill 是按名称加载的驾驶风格或行为原则文本，不直接调用 TraCI、推进仿真或提交动作。
例如 `human_reckless` 描述尽量保持速度、遇阻时积极考虑换道的倾向；模型仍须结合当前观测
决定动作，SUMO 安全规则和实际执行结果不由文字保证。

## Memory 记录和生命周期

每条记录由一次成功的下一步仿真结果生成，字段为：

| 字段 | 含义 |
|---|---|
| `observation` | 决策时该车的局部观测，包含车辆和仿真帧信息 |
| `requested_action` | 本轮为该车提交的动作；未提交时为空 |
| `next_state` | 下一帧该车的实际状态；不可用时为空 |
| `next_sequence` | 下一帧序号 |
| `events` | 下一帧中可关联到该车的事件，例如控制拒绝 |

每辆车维护独立的有界记录队列。首次决策通常没有历史；记录在对应步进成功返回之后写入。如果车辆不再出现在下一帧观测中，其记录会被清理；进程退出后全部清空。因此当前实现既不是长期记忆，也不会保留已离开车辆的历史。`max_steps` 指记录条数，不表示秒数。

## 配置自定义 Skill

默认使用内置的 `human_cautious`、`human_normal` 和 `human_reckless`。如需自己的 Skill，可在配置中指定目录，并在 binding 中填写名称：

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

例如配置文件位于 `configs/agent.yaml` 时，`my-skills` 指 `configs/my-skills/`，对应文件为 `configs/my-skills/calm_driver.md`。文件使用 UTF-8，内容是一段驾驶风格指引，例如：

```markdown
保持平稳驾驶。需要减速时尽量提前调整；信息不足时保持谨慎。
```

Skill 名称只能使用字母、数字、下划线和连字符，并以字母或数字开头，最长 64 字符；文件不能为空且不超过 16 KiB。指定自定义目录后，不会再搜索内置目录：如要混用，需要把所需文件也放入自定义目录。

## 接口

```python
class MemoryStore(Protocol):
    def read(self, experiment_id: UUID, vehicle_id: str) -> tuple[MemoryRecord, ...]: ...
    def append(self, experiment_id: UUID, vehicle_id: str, record: MemoryRecord) -> None: ...
    def retain(self, experiment_id: UUID, vehicle_ids: frozenset[str]) -> None: ...

class SkillProvider(Protocol):
    def load(self, name: str) -> SkillContent: ...
```

`MemoryRecord` 包含观测帧序号与仿真时间、车辆局部观测、模型请求的动作，以及下一帧
实际车辆状态和相关拒绝事件。请求动作和实际效果分开记录；没有拒绝事件不意味着换道完成。
车辆消失、实验停止或下一帧不可用时，实际效果应标为未知，而不是推测成功。

模型输入按车包含 `observation`、可选 `memory`、以及已加载 `skills`。Agent 以结构化
数据组织输入，具体模型适配器再转换为其协议。每一轮先取得局部观测，读取 Memory 和 Skill，
调用模型，校验并汇总动作，推进一次，然后依据返回帧更新 Memory。

自定义控制器可以保留原 `decide(observations)`，并额外实现以下接口以读取上下文：

```python
from trafficverse.agent_harness.context import AgentInput
from trafficverse.api.models import VehicleControlRequest


def decide_with_context(
    self, inputs: tuple[AgentInput, ...]
) -> tuple[VehicleControlRequest, ...]:
    for item in inputs:
        vehicle_id = item.observation.ego.vehicle_id
        memory = item.memory  # 未启用或读取失败时可能为 None
        skills = item.skills
        # 根据这辆车的观测、记忆和技能生成动作
    return ()  # 示例：不提交动作，由 SUMO 控制
```

这是自定义控制器类中的方法；类仍需提供 `decide`。当前内置本地、API 和 Jev 封装都支持上下文输入。

## 失败与边界

- Memory 关闭或读取失败时，本轮使用空记忆继续；Skill 加载失败时只忽略失败的 Skill。
- Memory 写入失败不影响已经执行的仿真步，也不重复提交动作；失败应记录日志。
- 多个 Skill 按配置顺序提供，模型提示中要求冲突时优先第一个；不保证模型一定遵守。
- 模型推理期间仿真按现有同步步进方式等待；模型失败或动作无效时该车不提交动作，交回 SUMO。
- 不把 Agent 记忆或模型推测当成 SUMO 权威状态；不承诺技能提示词一定产生特定驾驶效果。

内置文件为 `human_cautious`、`human_normal`、`human_reckless`，可通过顶层 `skills_directory`
选择自定义 UTF-8 Markdown 文件目录；相对路径基于配置文件，自定义目录不会搜索内置文件。
Skill 不存在、不可读、过大或名称非法时跳过该项并记录警告。关闭 Memory 且未配置 Skill 时
使用原有 `decide(observations)` 接口；开启任一功能后优先调用
`decide_with_context(inputs)`。如果自定义模型没有实现此接口，会记录警告并退回旧接口，
此时它无法使用 Memory 或 Skill。
