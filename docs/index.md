# SimHarness 文档

[简体中文](index.md) | [English](en/index.md)

SimHarness 是面向交通仿真的智能体 Harness。它使用 SUMO 推进交通，提供车辆观测、批量动作接口和桌面可视化；外部程序可以使用自己的模型或算法控制车辆。

## 按任务阅读

快速运行：参见[无界面运行仿真](HEADLESS_RUN.md)，一个命令启动场景和可选 Agent。

1. [安装与运行](GETTING_STARTED.md)：安装依赖、启动后端和桌面界面、运行 SUMO 场景。
2. [使用 Agent Harness](../src/trafficverse/agent_harness/README.md)：接入实验、选择车辆、配置本地模型、API、Jev 或自定义控制器。
3. [外部程序控制接口](EXTERNAL_STEP_API.md)：不使用内置 Agent Harness，直接调用同步步进 API。
4. [Memory 与 Skill](AGENT_MEMORY_SKILL_DESIGN.md)：理解按车记忆、驾驶风格和扩展接口。
5. [系统架构](ARCHITECTURE.md)：了解仿真、Agent、API 和界面之间的数据流。

## 其他资料

- [地图资产目录与支持格式](MAP_ASSET_CATALOG.md)：地图格式和资产中心设计。
- [Agent 开发指南](AGENT_DEVELOPMENT_GUIDE.md)：项目内部开发与验收要求，部分内容属于目标架构。
- [系统设计](SYSTEM_DESIGN.md)、[PRD](PRD.md)、[ADR](ADR.md)：项目设计和架构决策，包含历史背景与目标方案。
- [大模型车辆控制接口历史草案](LLM_VEHICLE_CONTROL_INTERFACE.md)：早期异步设计，**不是当前 Agent Harness 的使用说明**。

项目主流程为 SUMO 二维仿真。仓库包含 CARLA 联仿相关代码和设计，但当前桌面运行页面未接入 CARLA 三维窗口；真实联仿仍需集成和验证。
