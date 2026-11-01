# 外部车辆 Agent 框架

这个目录负责通过 TrafficVerse 的同步步进 API 运行车辆 Agent。空 `bindings` 可用于让全部车辆
按 SUMO 默认行为运行，检查接口链路。车辆选择与模型调用都在此进程中完成。

前提：启动包含 `external_stepping` 接口的 TrafficVerse API。桌面端进入“仿真配置”，勾选
“等待外部 Agent 控制”并开始仿真，从运行页复制实验 ID 到示例配置的 `experiment_id`。
待桌面端显示运行中后，从项目根目录运行：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m trafficverse.agent_harness --config configs/agent-harness.example.yaml
```

Manager 接入该实验后先用空动作取得局部观测，然后循环执行“按 ID 或等级选车 →
向各 Agent 提供其车辆的局部观测 → 一次提交动作 → 接收下一帧局部观测”。
`observation_radius_m` 设置邻车的水平距离半径（米，默认 100），包含恰好在边界上的车。
每辆车的观测包含自身状态和范围内其他车辆状态与距离，按距离和车辆 ID 排序；不推断前后车或车道关系。
`max_steps` 包含获取首帧的空动作步。运行结束、
异常或 Ctrl+C 时不会停止桌面端创建的实验，用户可在桌面端停止。单车被 SUMO 拒绝时输出警告。HTTP 步进超时不会自动
重试，避免同一步被重复推进；停止失败会明确报错。

为避免重复推进，按 ID 接入目前只支持已运行但尚未步进的外部控制实验；已执行过步骤的实验需要
重新创建。若 `experiment_id` 留空，仍可使用工作区、场景和地图配置由 Manager 自己创建实验，
此时 Manager 结束时会停止它创建的实验。
`step_delay_s` 可设置每步额外的现实时间间隔，便于观察；设为 0 时不额外等待。

`models/` 提供 `local.py:LocalModel`、`api.py:ApiModel` 和 `jev.py:JevModel`。
本地和远程模型都使用 OpenAI 兼容 Chat Completions HTTP 接口；`base_url` 是包含版本路径、
不含 `/chat/completions` 的地址。模型服务需返回文本 JSON，如
`{"actions":[{"vehicle_id":"car_001","desired_speed_mps":8.0}]}`；未返回车辆即由 SUMO 控制。
也接受完整包裹在 Markdown `json` 代码块中的 JSON，解包后仍执行同样的动作校验；不提取混有正文的片段。
每个 binding 的 `model` 指定 `base_url`、`model_id`、`timeout_s` 和可选 `api_key_env`。
凭证从环境变量读取，不写入 YAML。请求失败、超时、格式不合法或动作无效时，相应车辆交回 SUMO；
重复车辆动作全部丢弃该车的动作，越权车辆不执行，其他合法车辆可继续。
校验覆盖数据类型、有限数值、字段及动作组合，不证明驾驶安全或策略合理。

Jev 使用 OpenRouter `POST /api/alpha/decisions`，并非聊天接口；在 `base_url` 中填写
`https://openrouter.ai/api`，`model_id` 可配置为 `typesafe/jev-1.14`，默认读取
`OPENROUTER_JEV_SK`。每辆车从交回 SUMO、加速、减速、左换道、右换道、停车中选择；加减速
使用 `jev_acceleration_mps2`（默认 1 m/s²），动作转换和 SUMO 安全约束仍由程序执行。
Jev 的决策概率不等于驾驶安全保证，当前不根据置信度作额外判断。

例如用 Jev 控制一辆车时，将示例配置里的 `bindings: []` 替换为：

```yaml
bindings:
  - name: jev_driver
    wrapper: trafficverse.agent_harness.models.jev:JevModel
    vehicle_ids: [car_001]  # 换成场景中实际会出现的车辆 ID
    model:
      base_url: https://openrouter.ai/api
      model_id: typesafe/jev-1.14
      api_key_env: OPENROUTER_JEV_SK
      timeout_s: 30
      jev_acceleration_mps2: 1.0
```

如果绑定车辆暂未出现，不会调用模型；要控制多辆车可在 `vehicle_ids` 中继续添加 ID。
该接口在外部程序每个决策轮次会发起请求，使用 OpenRouter 时会消耗对应账户额度；
`max_steps` 和 `step_delay_s` 分别控制最大推进步数和额外的每步现实时间间隔。

所有封装实现 `models/base.py` 中的 `VehicleAgent`：
`decide(observations)` 接收分配车辆的 `VehicleObservation` 元组，并返回这些车辆的
`VehicleControlRequest` 元组。每个观测中 `ego` 是自身，`radius_m` 是观察半径，
`nearby_vehicles` 是邻车和水平距离；
时间、序号和实验 ID 可从 `ego` 读取。配置的 `wrapper` 使用
`Python模块:类名`；配置 `model` 时作为构造参数传入，未配置时仍按原无参数方式加载自定义类。
模型先顺序调用，再批量提交。返回空元组时，本次没有动作的车辆交给 SUMO。

Memory 和驾驶风格 Skill 可以按 binding 配置：

```yaml
bindings:
  - name: jev_driver
    wrapper: trafficverse.agent_harness.models.jev:JevModel
    vehicle_ids: [car_001]
    memory:
      enabled: true
      max_steps: 10
    skills: [human_cautious]
    model:
      base_url: https://openrouter.ai/api
      model_id: typesafe/jev-1.14
      api_key_env: OPENROUTER_JEV_SK
```

Memory 默认关闭；启用后按实验和车辆隔离，保留每车最近 `max_steps` 条决策及下一帧实际状态。
车辆不再出现在观测中时清理其记忆，Agent 进程结束后记忆消失；不提供持久化或跨实验记忆。
内置 Skill 为 `human_cautious`、`human_normal` 和 `human_reckless`。可在顶层设置
`skills_directory` 指向自己的目录，每个 Skill 为 UTF-8 的 `<名称>.md` 文件；相对路径相对于
配置文件。指定自定义目录后从该目录加载，不自动回退到内置目录。多个 Skill 按配置顺序传入，
提示模型冲突时优先第一个，但模型是否遵循无法保证。

每轮在当前局部观测之外，向模型提供对应车辆的记忆和 Skill 内容；模型响应后，推进一步，
再记录本轮请求、下一帧状态和该车相关事件。Memory 或 Skill 读取/写入失败时记录警告并继续。
旧版自定义 wrapper 可以继续使用 `decide(observations)`；要接收 Memory 和 Skill，需实现
`decide_with_context(inputs)`，否则记录警告并按旧接口继续。细节见
`docs/AGENT_MEMORY_SKILL_DESIGN.md`。旧的 `docs/LLM_VEHICLE_CONTROL_INTERFACE.md` 是不同异步方案的历史草案，
不描述当前同步 Agent Harness 的实现。

一辆车同时匹配多个绑定时，Manager 报错并停止，避免静默改变控制权。模型不能返回未分配车辆的
动作，同一车辆不能重复提交。等级依赖仿真快照的 `automation_level`；自定义场景应先验证等级标注。
观测是 SUMO 世界状态的范围筛选，不包含遮挡、传感器误差或车道拓扑判断。
