# SUMO / HighwayEnv 共享场景

## ADR-045 / ADR-046（Accepted）

ADR-045（2026-11-09）允许二维实验选择 SUMO 或 HighwayEnv；ADR-046（2026-11-11）
按用户要求进一步统一场景输入。每个实验只有一个交通真值源，只有 SimulationManager 推进
仿真时钟。Town04/CARLA Core Run 仍走原来的独立联仿路径。

桌面使用同一份原有二维场景列表：先选场景，再选 SUMO 或 HighwayEnv，保存后运行。
切换引擎不改变场景、智驾等级需求和时长。旧请求省略 `engine` 时仍默认 SUMO。
原生 `highway-standard` / `highway-light` 只保留为旧 API/CLI 预设，不进入桌面场景列表。

## 安装和启动

```powershell
uv sync --frozen --extra sumo --extra ui --extra highway
uv run --no-sync simharness serve --host 127.0.0.1 --port 8000
# 在另一终端启动桌面：
uv run --no-sync simharness traffic-ui --api-url http://127.0.0.1:8000
```

HighwayEnv 运行不需要 SUMO 可执行程序、TraCI 连接或 CARLA 服务。
无界面入口使用相同场景 ID 和独立引擎字段：

```yaml
schema_version: '1.0'
map_id: mixed-automation-obstacle
engine: highway
max_steps: 100
bindings: []
```

```powershell
uv run --no-sync simharness run --config configs/headless.highway.yaml
```

无界面 Agent 模式沿用已有外部逐步控制规则；特殊事件演示请使用桌面正常运行模式。
外部逐步控制由调用者提供车辆动作，不自动叠加桌面场景控制器，SUMO 与 HighwayEnv 一致。

## 输入保留和执行语义

保存配置时先生成与 SUMO 相同的场景快照，再转换为类型化 SharedScene；转换失败清理本次
快照并返回稳定错误。每次运行复制独立输入，保留选定引擎、seed、原始输入与转换结果。

- 保留原车道折线、宽度、限速、车道连接及路线顺序；展示继续使用同一份原始路网 GeoJSON。
- 保留车辆/路线/车道 ID、车长宽、最大速度、加减速、跟车间距、时距和发车时刻。
  确定性 flow 按 number、period 或 vehsPerHour 展开，保留 `flow_id.index` ID。
  发车口被占用时延迟插入；数值 departPos 和 base、数值 departSpeed 和 max 得到适配。
- 保留原场景步长、seed 与 begin 时间偏移。共享场景步长须能整除 1000 毫秒，
  且对应频率不超过 50 Hz。超出范围的场景可选择 SUMO。信号按静态周期、offset、原 linkIndex 执行，
  红/黄灯约束车辆停止；停车指令按指定车道、位置、duration/until 执行。
- 加塞、避障、紧急让行、汇入、偶发事故复用现有场景控制器和原 ID。支持速度、单步加速度、
  左右换道、停止、显式安全覆盖，以及 lane-change mode 0/512 的无间隙检查/保留碰撞间隙语义。
- 每辆车使用 HighwayEnv IDM 和车辆动力学，路径连接由转换路网决定；特殊事件与外部命令
  在每步前施加。事故不会像原生 highway-v0 的 ego 事故那样提前结束整个共享场景。
  到达路线末端移除车辆，时长到期结束；结束帧即使不在轨迹采样点也写入回放。

两引擎的跟车、换道轨迹、拥堵、碰撞和到达时间允许不同。车辆状态沿用米/秒和统一坐标，
HighwayEnv 内部 Y/heading 取反后转回场景坐标；位置按 HighwayEnv 车体中心输出。
这不是 SUMO 的物理等价实现，也不要求输出一致。

## 控制适配修正（2026-11-11）

- 显式 `safety_checks_override` 的设速命令在推进前生效，与场景脚本依赖的 TraCI
  speedMode=0 语义对齐；同时指定加速度时以单步加速度为准。普通目标速度仍使用 IDM。
  这类显式覆盖允许非物理的急减速，用于复用原演示脚本，不代表真实车辆制动性能。
- 持续制动最多减到零，不会变为倒车。安全换道（mode 512）受阻时保留纵向控制，
  在请求有效期内等待间隙；请求过期后不再自动重试。换道 duration 是请求有效期，
  不是 HighwayEnv 保证完成横向移动的时间。
- 救护车恢复高速前检查前车是否横向让出通道，避免最近车道 ID 刚变化就提前加速。
- 共享目标车道的汇入入口使用通行占用控制，当前车辆离开后释放；安全车辆同时受
  离散步长下的跟车位移约束保护。显式关闭安全检查的危险事件车辆不应用这层跟车保护。
  该适配不复刻 SUMO 全部路口优先权规则。
- 到达路线终点即退出，不再随机进入相连道路；显式车道连接不会因道路车道数不同而被重选。
- 同时指定 number 和 period 的车流保留原间隔；同时发车保留输入顺序。
  插入位置检查与实际位置一致，并检查闭合速度，空间不足时延迟发车。

可重复的场景检查使用 `scripts/dev/check_highway_scenarios.py`，输出写入
`artifacts/highway-scenario-check/`。它直接调用原场景控制器，覆盖桌面自动运行逻辑；
对让行/汇入场景检查碰撞，并要求匝道车辆确实进入主路，避免只用停止车辆来通过检查。

### 本次实际验证

使用仓库原始输入和固定 seed，每步执行同一份场景控制器；没有关闭碰撞检测。

| 场景 | 实际仿真时间 | 结果 |
|---|---:|---|
| 救护车让行 | 60 秒 / 1200 步 | 全场零碰撞，救护车通过路段；已加入完整分钟回归 |
| 高自动化汇入 | 45 秒 / 900 步 | 零碰撞，11 辆匝道车进入主路，104 辆车到达终点 |
| 低等级汇入 | 45 秒 / 900 步 | 零碰撞，6 辆匝道车进入主路，106 辆车到达终点；155 辆均已发车 |
| 加塞 | 30 秒 / 600 步 | 无有效车辆命令被拒绝；仍有危险事件及后续交通碰撞 |
| 避障 | 45 秒 / 900 步 | 无有效车辆命令被拒绝；低等级避障失败的事故保留 |
| 偶发事故 | 90 秒 / 1800 步 | 无有效车辆命令被拒绝；已覆盖 60 秒后新增 L5 车辆阶段，事故保留 |

其他证据：69 项相关单元/契约测试、18 项真实 HighwayEnv 集成检查通过；相关 Ruff
format/check 与 mypy（12 个文件）通过。回归包括立即设速、加速度优先、连续刹车不倒退、
换道受阻仍减速/有效期内重试/过期取消、路线结束、车流间隔、汇入通行和占用释放。
普通 image2road 路网有加载/单步及独立信号停车测试，本次未进行整张城市路网的长时间验收。

SUMO 1.26.0 也对照运行了救护车场景 60 秒：新旧让行判据均没有救护车碰撞，
均记录同一对普通车辆 `yield_L1_001` / `yield_L4_000` 侧碰。首次以“SUMO 全场零碰撞”为
断言的对照检查因此失败；复核旧判据后确认是原场景已有结果，不宣称 SUMO 全场无碰撞。
事故场景没有做碰撞数量或等级分布的跨引擎等价验收。

更新后应重启后端并重新保存场景配置，再创建新实验；已经保存的不可变快照不会自动重算
发车计划。现有两引擎的配置/协议字段不变。

## 支持范围

当前仓库 7 个二维 SUMO 场景均纳入真实加载/单步检查，另有信号停车、延迟发车、停车恢复、
路线完成及 API 保存/运行/回放的小型真实测试。没有进行长时间全场景性能或交通结果等价验收。

转换器支持当前场景使用的显式 vehicle/route、确定性 flow、静态信号和车辆 stop。
自适应信号、概率 flow、动态 routeDistribution、行人和动态路线需求不支持，明确拒绝转换。
第三方新增 SUMO 场景仍需验证其输入是否落在这个范围。路口让行不复刻 SUMO 的完整规则。
车辆 carFollowModel/sigma 等引擎专属参数由 HighwayEnv IDM 行为替代。

正式运行支持原路网预览、Parquet 回放与已有历史/导出接口。SUMO XML 专属汇总指标在
HighwayEnv 中不产生；缺失指标不伪造。CARLA 联仿未扩展到 HighwayEnv。

## 契约兼容

生产者：桌面配置页及无界面入口；消费者：配置 REST、EngineConfigurationStore、启动装配。
`SimulationConfigurationDraft.engine` 是可选字段，默认 sumo，已有客户端兼容。
默认 `GET /api/v1/maps` 沿用原有字段，列出 SUMO 和 Core Run 地图。
`GET /api/v1/maps?include_engine_metadata=true` 增加原生 HighwayEnv 预设及引擎步长、时长字段。
原生预设的保存时长必须为策略步长的整数倍，最长一小时；非法时长返回 HTTP 422。
内部 HighwayEnv 配置增加可选 shared_scene；模型和生成 schema 同步发布。
部署顺序为后端/契约先行、UI 随后；旧保存配置继续按原引擎读取。

## Windows 中文路径

Python 3.10 编辑安装路径解码失败时，使用 `python -X utf8` 或启用终端 Python UTF-8 模式。
本机 `.pth` 已修复为相对路径，后续重建环境可能需要再次处理。

参考：[HighwayEnv](https://github.com/Farama-Foundation/HighwayEnv)、
[官方动作接口](https://highway-env.farama.org/actions/)。
