# 外部车辆 Agent 框架

这个目录负责通过 TrafficVerse 的同步步进 API 运行车辆 Agent。模型封装尚未实现；空 `bindings`
可用于让全部车辆按 SUMO 默认行为运行，检查接口链路。车辆选择与模型调用都在此进程中完成。

前提：启动包含 `external_stepping` 接口的 TrafficVerse API。桌面端进入“仿真配置”，勾选
“等待外部 Agent 控制”并开始仿真，从运行页复制实验 ID 到示例配置的 `experiment_id`。
待桌面端显示运行中后，从项目根目录运行：

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m trafficverse.agent_harness --config configs/agent-harness.example.yaml
```

Manager 接入该实验后先用空动作取首帧，然后循环执行“按 ID 或等级选车 →
请求各 Agent → 一次提交动作 → 接收下一帧”。`max_steps` 包含获取首帧的空动作步。运行结束、
异常或 Ctrl+C 时不会停止桌面端创建的实验，用户可在桌面端停止。单车被 SUMO 拒绝时输出警告。HTTP 步进超时不会自动
重试，避免同一步被重复推进；停止失败会明确报错。

为避免重复推进，按 ID 接入目前只支持已运行但尚未步进的外部控制实验；已执行过步骤的实验需要
重新创建。若 `experiment_id` 留空，仍可使用工作区、场景和地图配置由 Manager 自己创建实验，
此时 Manager 结束时会停止它创建的实验。
`step_delay_s` 可设置每步额外的现实时间间隔，便于观察；设为 0 时不额外等待。

`models/` 内已预留 `local.py:LocalModel`、`api.py:ApiModel` 和 `jev.py:JevModel` 三个封装类。
它们实现 `models/base.py` 中 `VehicleAgent` 的方法签名，但调用 `decide` 时会明确抛出
`NotImplementedError`；暂不支持真实推理。后续填写每类模型的调用逻辑即可：
`decide(snapshot, vehicles)` 返回分配车辆的 `VehicleControlRequest` 元组。配置的 `wrapper` 使用
`Python模块:类名`，当前通过无参数构造函数加载；具体模型参数、凭证和网络协议留待实现模型时设计。
模型先顺序调用，再批量提交。返回空元组时，本次没有动作的车辆交给 SUMO。

一辆车同时匹配多个绑定时，Manager 报错并停止，避免静默改变控制权。模型不能返回未分配车辆的
动作，同一车辆不能重复提交。等级依赖仿真快照的 `automation_level`；自定义场景应先验证等级标注。
当前接口没有专门的邻车距离等完整驾驶观察；模型可以读取快照中的现有车辆状态。
