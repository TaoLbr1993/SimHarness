# 外部程序同步控制车辆

[English](en/external-step-api.md)

本文面向希望直接通过 HTTP 将自己的算法接入仿真的开发者。若希望复用车辆分配和大模型封装，可使用 [Agent Harness](../src/trafficverse/agent_harness/README.md)。

## 接口概览

| 接口 | 作用 | 返回 |
|---|---|---|
| `POST /api/v1/experiments/{id}/step` | 提交动作并推进一步 | 全局仿真帧 |
| `POST /api/v1/experiments/{id}/local-step` | 提交动作并推进一步 | 所选车辆的局部观测和事件 |

两者都要求实验开启 `external_stepping` 并处于运行中。它们推进的是同一个实验，不能把 `/local-step` 当作只读查询：**每次成功请求都会推进一步**。需要纯观察或其他客户端实时展示时，可使用已有的全局 WebSocket 消息。

每次请求的动作基于上一帧；`expected_sequence` 必须是上一帧序号。外部客户端负责选择车辆、生成动作并决定下一步何时开始。客户端尚未提交动作时仿真会等待。

## 局部观测步进

`POST /api/v1/experiments/{id}/local-step` 执行与 `/step` 相同的一次同步步进，
但返回指定车辆的局部观测，不返回全局车辆帧。请求示例：

```json
{
  "expected_sequence": 0,
  "actions": [],
  "radius_m": 100.0,
  "vehicle_ids": ["car_001"],
  "automation_levels": [],
  "all_vehicles": false
}
```

`vehicle_ids` 和 `automation_levels` 是并集；`all_vehicles: true` 选择当前所有车辆。
未选择任何车辆时返回空观测数组，仍会正常推进。选择 ID 不在本帧时不会产生该车观测。
响应的 `experiment_id`、`sequence`、`simulation_time_ms` 与该步全局帧一致；
`observations` 中每项含 `ego`、`radius_m` 和 `nearby_vehicles`，后者每项包含 `vehicle` 和 `distance_m`。
`events` 保留该步事件。距离是 SUMO 平面 XY 坐标的欧氏距离（米），包含边界，不含自身；
邻车按距离、车辆 ID 排序。这只是范围筛选，不代表可见性、前后关系或换道安全。
半径必须为有限正数，请求校验失败不会推进。原 `/step` 和 WebSocket 全局帧保持不变。

`ego` 和每个邻车的 `vehicle` 都是车辆状态，包含 `vehicle_id`、`position`、`speed_mps`、`acceleration_mps2`、`heading_rad` 和 `lane_id` 等字段。`simulation_time_ms` 是仿真时间，`distance_m` 和 `radius_m` 以米为单位。

一个无需第三方 HTTP 库的局部观测示例如下。先通过桌面端创建并开始一个**尚未步进**的外部控制实验，替换实验 ID 和车辆 ID 后运行。示例只发送空动作，方便检查连接；不会控制指定车辆。

```python
import json
from urllib.request import Request, urlopen

base_url = "http://127.0.0.1:8000"
experiment_id = "替换为实验ID"
vehicle_id = "替换为实际车辆ID"
url = f"{base_url}/api/v1/experiments/{experiment_id}/local-step"
sequence = 0

for _ in range(10):
    payload = {
        "expected_sequence": sequence,
        "actions": [],
        "radius_m": 100.0,
        "vehicle_ids": [vehicle_id],
    }
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=30) as response:
        frame = json.load(response)
    sequence = frame["sequence"]
    print("sequence:", sequence, "observations:", len(frame["observations"]))
```

车辆尚未进入仿真时观测数可以为 0。需要控制时，根据上一帧的车辆状态填入下一次请求的 `actions`；可用动作见下文。示例运行完后请在桌面端停止实验。

## 全局帧步进

创建实验时设置 `external_stepping: true`，由外部程序逐步提交车辆动作。实验启动后会等待请求，
不会按墙上时间自动推进。每次成功的 step 请求恰好推进一个 SUMO 步长，并返回该步的完整仿真帧。
此模式中不运行内置混合智驾场景规则控制器，不启用 SimHarness 的碰撞车辆冻结；未指定动作的车辆
由 SUMO 原生模型控制。SUMO 仍决定动作的实际效果并执行其安全约束。

1. 用现有 REST API 创建或选择工作区和 SUMO 场景。
2. 调用 `POST /api/v1/experiments`，在原有字段之外传入 `external_stepping: true`。
3. 依次调用 `POST /api/v1/experiments/{id}/prepare`、`POST /api/v1/experiments/{id}/start`。
4. 初始无车辆快照，提交 `POST /api/v1/experiments/{id}/step`，内容为
   `{"expected_sequence": 0, "actions": []}`。这会推进一个原生 SUMO 步并返回车辆状态。
5. 使用响应中的 `traffic.sequence` 与 `traffic.vehicles` 构造下一批动作，再调用 step。
   同一批未出现的车辆由 SUMO 自行处理；空批次使所有车辆按 SUMO 原生方式推进一步。

示例请求：

```json
{
  "expected_sequence": 1,
  "actions": [
    {"vehicle_id": "car_001", "desired_speed_mps": 10.0},
    {"vehicle_id": "car_002", "desired_acceleration_mps2": -1.0},
    {"vehicle_id": "car_003", "lane_change": "LEFT"}
  ]
}
```

每个车辆 ID 在批次中只能出现一次。每车可请求目标速度、目标加速度、
相对换道或停车；速度、加速度与停车不能互相组合。换道请求有效时间为一个仿真步，不保证
SUMO 一定执行换道。使用速度或停车的车辆在下一批缺少此类动作时，会先通过 TraCI 显式释放速度
覆盖；加速度和换道请求限于一个仿真步。车辆不在上一帧或单车命令被 SUMO 拒绝时，其他车辆的
合法命令仍会执行，响应帧的 `events` 包含 `vehicle.control.rejected` 及车辆 ID 和原因。单车命令
可能部分提交，无法事务回滚；若控制覆盖无法安全释放，实验失败。请求格式校验失败或序号过期时不推进。

| 字段 | 含义 | 约束 |
|---|---|---|
| `vehicle_id` | 被控制车辆的 ID | 必填；同一批次不能重复 |
| `desired_speed_mps` | 目标速度，米/秒 | 非负；不能与目标加速度或停车同时使用 |
| `desired_acceleration_mps2` | 目标加速度，米/秒² | 不能与目标速度或停车同时使用 |
| `lane_change` | 请求相对换道 | `LEFT`、`RIGHT` 或默认 `NONE`；可能受 SUMO 约束而不执行 |
| `stop_requested` | 请求停车 | `true` 时不能同时指定速度或加速度 |

一条动作必须至少包含一个有效控制意图。`takeover_requested` 不属于同步步进动作，不可在此接口中启用。换道可与速度或加速度请求同时出现。

`expected_sequence` 必须与最近一次返回的 `traffic.sequence` 一致，初始为 0；过期或重复请求
返回冲突，避免重复推进。若响应丢失，可通过现有 WebSocket `world.snapshot.request` 查询最新帧
及序号，再决定下一步。外部程序自己负责选择哪些车辆交给哪个 Agent，模型推理期间无需提交请求，
仿真会保持等待。此接口不实现模型调用、车辆绑定或按等级筛选。
