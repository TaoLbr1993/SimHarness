# 外部程序同步控制车辆

创建实验时设置 `external_stepping: true`，由外部程序逐步提交车辆动作。实验启动后会等待请求，
不会按墙上时间自动推进。每次成功的 step 请求恰好推进一个 SUMO 步长，并返回该步的完整仿真帧。
此模式中不运行内置混合智驾场景规则控制器，不启用 TrafficVerse 的碰撞车辆冻结；未指定动作的车辆
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

`expected_sequence` 必须与最近一次返回的 `traffic.sequence` 一致，初始为 0；过期或重复请求
返回冲突，避免重复推进。若响应丢失，可通过现有 WebSocket `world.snapshot.request` 查询最新帧
及序号，再决定下一步。外部程序自己负责选择哪些车辆交给哪个 Agent，模型推理期间无需提交请求，
仿真会保持等待。此接口不实现模型调用、车辆绑定或按等级筛选。
