# 无界面运行仿真

[English](en/headless-run.md) · [文档入口](index.md)

无需启动桌面或单独启动后端，通过一个命令运行 SUMO 场景和可选的车辆 Agent：

```bash
uv run trafficverse run --config configs/headless.example.yaml --scenario image2road
```

需要提前安装 Python 依赖和 SUMO。程序优先使用 `PATH` 中的 `sumo`，找不到时会尝试使用当前 Python 环境中已安装的 `sumo` 包内的可执行文件。`--scenario` 是 `configs/maps/` 中已发现的 SUMO 场景 ID，不是任意 `.sumocfg` 文件路径；省略时使用配置文件的 `map_id`。

程序在本机临时启动后端，自动创建和启动实验，等待 Agent 每轮决策并推进 SUMO，运行结束后停止实验和后端。无需手动填写工作区或实验 ID。运行过程不会连接或停止已有后端。

## 配置场景和 Agent

复制 [`configs/headless.example.yaml`](../configs/headless.example.yaml) 并按需编辑：

```yaml
schema_version: '1.0'
map_id: image2road
max_steps: 100
step_delay_s: 0
request_timeout_s: 30
observation_radius_m: 100
bindings: []
```

`max_steps` 包括用于获取初始观测的第一个空动作步。空 `bindings` 不调用模型，所有车辆由 SUMO 控制，且结果中没有选定车辆的局部观测。若仿真先达到结束条件，实际步数可以少于 `max_steps`。

模型绑定使用现有 Agent Harness 配置，例如：

```yaml
bindings:
  - name: api_driver
    wrapper: trafficverse.agent_harness.models.api:ApiModel
    vehicle_ids: [car_001]  # 替换为场景中实际会出现的车辆 ID
    model:
      base_url: https://example.com/v1
      model_id: your-model
      api_key_env: MODEL_API_KEY
      timeout_s: 30
    memory:
      enabled: false
      max_steps: 10
    skills: []
```

将密钥放在运行进程的环境变量中；YAML 只填变量名：

```powershell
$env:MODEL_API_KEY = '<your-api-key>'
```

Linux/macOS 使用 `export MODEL_API_KEY='<your-api-key>'`。`vehicle_ids` 可以选择多辆车，也可以使用 `automation_levels`；同一车辆匹配多个 binding 会报错。可以配置本地 OpenAI 兼容服务、远程 API、Jev 或自定义控制器，具体字段见 [Agent Harness 使用说明](../src/trafficverse/agent_harness/README.md)。如设置 `skills_directory`，相对路径以无界面配置文件所在目录为基准。Memory 和 Skill 默认不启用。

## 运行结果

每次运行创建 `artifacts/headless/<运行ID>/`：

- `steps.jsonl`：每个成功步进一行 JSON，包含实际提交的 `request` 和返回的 `response`。其中可查看动作、所选车辆的局部观测和相关事件；第一行为获取初始观测的空动作步。
- `summary.json`：实验 ID、场景 ID、最终实验状态、步数、仿真时间及输出路径。
- 终端也输出相同的运行摘要。SUMO 运行文件和回放数据位于摘要所指向的 `artifacts/sumo/<实验ID>/`。

结果记录的是已提交动作和 SUMO 返回的状态，不包含模型原始推理过程，也不能仅凭请求动作判断它已成功执行。模型失败或返回无效动作时，对应动作会被省略；检查日志和返回事件，结合下一帧状态判断结果。若运行中断或失败，已有的 `steps.jsonl` 保留，`summary.json` 仅在正常完成后生成。

远程模型请求可能消耗账户额度。不要将 API Key 写入 YAML 或提交到仓库。
