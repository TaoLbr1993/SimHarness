# 路网生成 Demo（Road Network Generation Demo）

基于大语言模型 Agent 自动生成路网，并导出为 SUMO 格式的后端 Demo。

## 整体流程

```
用户自然语言描述（"我要一个十字路口"）
        ↓
  FastAPI 后端（main.py）
        ↓
  解析带原文引用的验收清单（一次解析，重试期间保持不变）
        ↓
  Agent 生成调用 roadgen 的 Python 代码（agent.py）
        ↓
  独立任务目录中执行代码（runner.py）
        ↓
  roadgen 导出 SUMO PlainXML → netconvert 编译并检查 .net.xml
        ↓
  从 .net.xml 渲染全图、路口放大图及受控车道的信号灯图标
        ↓
  程序执行 XML 验收规则 + 模型辅助检查图像几何
        ↓
  合并已确认结构问题、疑似视觉问题和未验证要求
        ↓
  检查失败时反馈代码、图片、问题及原因，重新生成（最多三次）
        ↓
  返回每次尝试、检查结果与生成文件列表
```

## 目录结构

```
backend/
├── main.py            # FastAPI 入口：路由、CORS、异常处理
├── agent.py           # Agent：自然语言 → 可执行的 Python 代码
├── runner.py          # 执行生成代码，隔离每次任务并收集产物
├── renderer.py        # 从编译后的 SUMO 路网渲染检查图片
├── review_models.py   # 有限的验收规则和证据模型（不是 V2 RoadSpec）
├── xml_checks.py      # 对编译后的 XML 执行确定性规则
├── review_pipeline.py # 汇总证据、处理冲突并驱动自动修复
├── config.py          # 配置读取（从 .env 读取，不写死敏感信息）
├── requirements.txt   # Python 依赖
└── .env.example       # 环境变量模板（复制为 .env 后填写）
```

## 环境要求

- Python 3.10+
- 依赖：FastAPI、Uvicorn、OpenAI 兼容客户端、python-dotenv
- roadgen：Linux 提供预编译 wheel，Windows 请使用 WSL 或自行准备编译环境
- SUMO 的 `netconvert` 必须位于 PATH；其版本应与目标仿真环境匹配
- 配置的模型必须支持 OpenAI 兼容的图像输入；Pillow 用于生成 PNG

roadgen 项目地址：https://github.com/hakuturu583/hdmap_generator

## 快速开始

```bash
cd backend
pip install -r requirements.txt

# 复制配置模板，填写模型服务信息
cp .env.example .env
#   编辑 .env，填入 MODEL_BASE_URL / MODEL_API_KEY / MODEL_NAME

python -m uvicorn main:app --reload --port 8000
```

启动后访问：

- 接口文档（Swagger）：http://127.0.0.1:8000/docs
- 健康检查：http://127.0.0.1:8000/health

## 接口说明

### POST /generate

请求：
```json
{ "description": "一个十字路口，两条双向两车道的道路垂直相交" }
```

从 Demo 0.2.0 起，可选字段 `review_mode` 默认为 `rules_and_image`：
先由模型将自然语言整理为有原文引用的验收项，再由程序解析编译后的 XML 执行这些规则。
每次有效生成仍渲染图片，辅助模型检查道路形状、明显错位和异常折返。

`image_and_facts` 和 `image_only` 保留原有行为，用于对照实验；它们不经过新的需求规则检查。
显式设为 `image_only` 时，需求审核模型只收到
需求文字、通用读图说明和 PNG 图片，不接收网络摘要、XML、生成代码或历史审核结论。例如：

```json
{ "description": "生成一个四向十字路口，必须有信号灯。", "review_mode": "image_only" }
```

`image_only` 模式仍先在本地执行 `netconvert` 和基础结构检查，但不把其事实数据发送给审核模型。
生成/修复模型仍接收需求、先前代码、问题原因及可用的图片。每轮审核独立调用，
`review.evidence_mode` 记录该轮实际使用的模式。

默认流程的验收规则覆盖：

| 规则 | 程序实际检查 |
|---|---|
| `junction_count` / `approach_count` | 交叉口数量、每个路口的相邻道路分支数量 |
| `signals` | 全网存在/不存在有效信控，或指定进口的所有转向及所有进口车道都有有效信控连接 |
| `lane_count` | 每个所选方向的进口、出口车道数，分别核对 |
| `turn` | 所选进口至少存在一个指定转向，或完全禁止该转向 |
| `connected` | 道路图是否弱连通；不等于车辆在任意两点之间可达 |
| 固定完整性检查 | 道路和车道引用、连接端点、信号程序非空且相位长度一致、正时长、相位索引有效 |

一个 `tlLogic` 存在不代表四向覆盖通过；某个进口仅有一个受控转向也不够。
普通“有信号灯”按信号控制解释，实体灯杆数量/安装位置、配时安全等额外要求不在这些规则的验证范围。
方向使用相邻节点的 SUMO XY 坐标（北=+Y，东=+X），偏离坐标轴超过配置阈值 30° 或同侧有多个进口时，
明确方位规则标为未验证。同一对节点间的平行道路也不会静默求和为一个进口。
未指定方向时 `all_approaches` 检查实际进口，不强迫只有出口的单向分支增加进口灯；
若需求明确指定某方向必须有受控进口，则不存在该进口也会失败。
`scope=single` 要求交叉口唯一；多个路口无法唯一定位时标为未验证，不能任意挑一个通过。

响应：
```json
{
  "status": "success",
  "description": "...",
  "review_mode": "rules_and_image",
  "model_configured": true,
  "review_plan": {"rules": [], "visual_requirements": ["..."], "unverified_requirements": []},
  "generated_code": "...",
  "execution": {
    "exit_code": 0,
    "stdout": "",
    "stderr": "",
    "elapsed_sec": 1.23,
    "artifacts": ["output/validated.net.xml", "output/review.png"],
    "timeout": false
  },
  "validation": {"passed": true, "issues": [], "network": "output/validated.net.xml", "image": "output/review.png"},
  "rule_review": {"passed": true, "source": "compiled_xml", "checks": [], "approaches": []},
  "visual_review": {"source": "image", "certainty": "suspected", "issues": []},
  "review": {"passed": true, "issues": [], "issue_details": []},
  "attempts": [{"attempt": 1, "generated_code": "...", "execution": {}, "validation": {}, "review": {}}]
}
```

`success` 要求模型已配置、生成代码成功执行、PlainXML 文件存在且可解析、
`netconvert` 成功编译出非空路网、图片渲染成功。默认模式还要求验收规则通过、没有未验证要求和未解决的视觉疑点；
两个旧实验模式仍由其模型审核决定需求是否满足。
`attempts` 保存每次生成代码、执行结果、结构检查和需求检查；检查失败的诊断会反馈给下一次生成。
默认模式返回 `review_plan`（可核对的需求解释）、`rule_review`（逐条通过/失败/未验证及 XML 证据）、
`visual_review`（原始图像疑点）和汇总 `review`。不依赖模型宣称 XML 正确。
`review.issue_details` 中每项都包含 `problem`（问题）和 `reason`（问题依据）；默认模式还包含
`source` 和 `certainty`：XML 规则失败为 `xml/confirmed`，视觉发现为 `image/suspected`。`review.issues`
保留问题与原因拼接的字符串以兼容现有调用方。图像可用时，重试会同时发送该图像和问题原因；
若代码报错或未生成可编译网络，则没有图片，使用文本诊断重试。模型不支持图像或图片渲染失败时
不会降级为仅文本并宣称通过。
超时、执行失败和检查失败分别返回 `execution_timeout`、`execution_error` 和
`validation_error`。模型未配置或生成模型调用失败返回 `agent_error`；视觉检查调用或响应格式
异常返回 `validation_error`，保留已经完成的 XML 检查。
无法覆盖的要求、无法确定的检查对象或重试后仍未解决的纯视觉疑点返回 `review_required`，不会伪装为成功或已确认的结构错误。

结构冲突不会按模型一句话决定。当前自动裁决只覆盖明确的信控谓词和空间范围：
必须是相同的 presence/absence/coverage，并且 coverage 能对应同一个唯一路口和所需方向。
例如全网“存在信号控制”的通过，不能用来驳回“东侧未受控”的疑点。
符合精确匹配条件的疑点保留在 `review.adjudications`，附对应 XML 证据，修复模型只收到已确认的问题与已通过的规则；
其他无法精确匹配的结构疑点保持未验证。图像几何疑点可触发修复，但不会改标为已确认。

验收计划的自然语言解释仍依赖模型，原文引用只能防止编造来源，不能证明模型没有漏掉或误解需求。
验收清单 JSON、字段或原文引用不合格时，反馈错误并最多纠正重试一次；再次不合格仍返回失败，
首次失败原因写入服务日志，不能绕过后续检查。
调用方可查看 `review_plan`；当前规则也不能保证交通规则、语义或仿真行为完全正确。图片是从
编译后的 SUMO 几何绘制的二维示意图，仅放大一个路口；缺失车道宽度时使用默认宽度绘制。
图片用三色信号灯图标标出由 `connection.tl` 关联到具有相位的 `tlLogic` 程序的非内部进口车道；
同一车道的多个转向只画一个灯图标。仅有 `junction.type=traffic_light` 或无车道绑定的控制器不会画灯。
图标与车道之间有引线；它表示该车道存在受控连接，不表示所有转向都受控，也不代表真实灯杆位置。
三色图标是静态符号，不代表当前灯色、同时放行或配时正确。无灯图和有灯图使用同一图例，不添加判定标签。
图片不表达高程和信号相位，不能仅凭图片确认精确宽度、立交高度、全部路口细节或信号运行安全；
只看图审核可检验可见的缺灯问题，不能替代结构或仿真检查。
当前路网摘要限制
为 64 条非内部道路、64 个非内部节点、128 条连接和 64 个信号灯，超出时拒绝自动判定。每个尝试有独立目录，
但子进程不是安全沙箱；它仍以服务用户权限执行模型生成的代码，不能将该接口开放给不受信任的用户。
子进程环境不传递包含 API_KEY、TOKEN、SECRET、PASSWORD、CREDENTIAL 的变量，但这不是完整隔离。
需求文本、生成的代码、运行诊断、路网图片及摘要会发送给配置的模型服务，可能包含本机路径等信息；
部署时应确认该服务可接收这些内容。生成或需求检查的模型调用异常会立即结束，不进入代码修复重试。

## 真实地点路网（实验功能，v0.3）

`POST /generate` 默认 `source_mode="auto"`，由模型区分抽象路口与真实地点。
例如：

```json
{
  "description": "生成北京航空航天大学学院路校区附近约 1 公里半径的真实路网信息作为测试"
}
```

真实地点流程：语言提取地名和范围 → Photon 查询实际候选 → 模型按名称、城市和校区选择
候选 ID → Overpass 获取 OpenStreetMap 道路和转向限制 → `netconvert` → 全量 XML 检查
→ SUMO 空车流加载检查 → 简化预览 → 用户判断是否符合需求。模型不生成经纬度和真实道路几何。
模糊地点或无候选返回 `needs_clarification`，附 `candidates` 和原因。调用方可补充描述，或在
相同请求增加 `selected_place_id` 选择返回的候选 ID（OSM 或 `amap:<hash>`）；接口会重新核对候选集合。

显式 `source_mode="synthetic"` 使用原 roadgen 生成、检查、修复流程；`real_map` 强制要求
真实地点。旧 `review_mode` 仅用于合成分支，真实分支固定为 XML/加载检查和用户判断，响应中记录
`review_mode="xml_and_user"`。语言意图与候选选择仍可能误解，响应公开 `intent`、
`place_choice.reason`、`place`、坐标和候选供核对，不以模型置信度替代地名证据。
Photon 地名检索优先使用已知全称，并合并主查询与英文备选查询的候选（最多两次查询、每次五个，
按 OSM ID 去重）。首批搜到大厦、科技园等无关对象不会阻止备用检索。查询来源与失败诊断
保留；多城市或多校区仍无法确定时要求补充信息，不固定坐标或默认选择第一项。

默认 `detail_level="auto"` 按语言选择详细度：未要求细节时为 `main_roads`，只导入
OSM motorway/trunk/primary/secondary/tertiary 及其 link 连接道路，省略住宅道路、支路、
服务道路等。这里“主干路”指主要道路骨架，并不等同于严格的市政道路等级。
简图显示道路走向、连接和名称，不显示车道标线或信号图标；SUMO 文件仍保留运行需要的车道与信控。
用户说“包含支路”“更详细”“显示车道/信号细节”时，选择 `detailed`，导入所有 passenger
可用道路并生成局部细节图。也可显式传 `detail_level="main_roads"` 或 `"detailed"` 覆盖语言判断。
详细版扩展数据和展示层级，不代表实地车道或信号信息已被校准。

范围默认 1000 米并在响应中说明，目前接受 100–3000 米，不自动缩小超范围请求。
圆心使用选定地点服务返回的代表点（在 `place` 明示），不是沿校园边界向外缓冲 1 公里。
Overpass 按中心周边圆形选取道路，原始文件保留完整 OSM way 和相关限制。
转换先在 OSM 几何节点细分道路，再从导入的 SUMO 网络中用 36 点近似圆筛选道路段、简化中间节点；
保留的段不在圆边界精确截断，因此实际边界可能超出半径。
`validation.statistics.converted_xy_bounds` 记录实际 SUMO XY 范围，完整选择多边形在
`conversion-command.json` 的两次调用中。实际边界从最终车道几何计算；XML location 元数据另存
`xml_location_xy_bounds`，不能把它当成裁选后的真实边界。它不是精确 GIS 裁剪，也不是校园围墙边界。

只导入 `passenger` 可用的机动车路网，保留孤立分量并报告数量；不保证校园/私人道路合法通行，
不保证任意 OD 可达。OSM 数据可能不完整或过时，缺少的车道数、限速、优先权、车道连接及信号配时
可能由 SUMO 推断；`source_ways_with_lanes/maxspeed` 是原始 highway way 标注数量，
不是导入后道路覆盖率。信控节点和灯图标只表示 SUMO 绑定，不证明实地灯杆或当前配时。

真实地图检查不使用合成分支 64 条道路的摘要上限；所有外部道路、车道连接和控制器都参加现有
完整性规则，随后由 SUMO 加载检查。该检查仍不证明交通规则、配时冲突或现实地理精度。
真实地图不调用视觉模型判定是否符合需求，由用户查看预览判断。转换器丢失转向限制、无法构建
信号程序或源信号无连接时，保留 `validation.import_issues` 和日志，供用户查看，不单独阻断交付。
额外的改道/车道/信控/CARLA/车流等要求在 `unverified_requirements` 和 `intent.extra_requirements`
列为未验证，不自动改写真实道路。真实分支没有 roadgen 重设计兜底。

输出目录 `backend/workspaces/<task_id>/output/`：

| 文件 | 用途 |
|---|---|
| `source.osm.xml`、`overpass-query.txt`、`geocoder-*.json` | 来源数据与实际查询 |
| `validated.net.xml`、`map.sumocfg` | SUMO 路网和无交通需求的加载测试配置 |
| `map_preview.png`、`review.png` | 默认主干路简图；只有详细版生成带信控图标的局部放大图 |
| `metadata.json` | 意图、地点、来源时间、是否缓存、检查结果、统计及限制 |
| `conversion-command.json`、`netconvert-version.txt`、`netconvert.log`、`sumo-load.log` | 可复现转换参数和完整诊断 |

接口响应 `artifact_urls` 给出 `GET /artifacts/<task_id>/<filename>` 下载地址；只允许任务 output
目录下的文件。`success` 表示路网与预览生成、基础检查及加载完成，不表示现实准确性或用户需求已验证。
此时 `user_review="pending"`，`user_review_note` 提醒用户看图判断是否符合测试需要。
`source_error` 表示地图/地名服务失败，`validation_error` 表示转换/结构/加载失败；所有情况尽可能
保留 metadata 和诊断。无模型可用时，仍按原入口返回 `agent_error`。
`validation.passed` 仅表示 XML 和 SUMO 加载通过，`visual_review=null` 表示未调用视觉审核。
工具超时保留部分输出；格式/引用/转换/加载错误仍返回失败。

无需新增 Python 依赖。需要 `netconvert`、`sumo` 和可联网的模型/地图服务；自动查找安装目录的
SUMO XSD 数据，也可设置 `SUMO_HOME`。`.env.example` 列出地图服务地址、半径、超时、大小和缓存设置。
单进程内串行请求公共地图服务，缓存 24 小时；遇到限流不立即重试。不把公共演示服务当成生产 SLA。
地名与检索内容发送给配置的地名服务，坐标/半径发送给地图服务；真实分支仅将描述、候选发送给模型。

来源与政策：[Photon](https://github.com/komoot/photon)、
[Overpass 公共实例使用说明](https://dev.overpass-api.de/overpass-doc/en/preface/commons.html)、
[SUMO OSM 导入](https://sumo.dlr.de/docs/Networks/Import/OpenStreetMap.html)、
[Nominatim 公共服务政策](https://operations.osmfoundation.org/policies/nominatim/)。
地图及图片保留 [© OpenStreetMap contributors / ODbL](https://www.openstreetmap.org/copyright) 署名。
本实验入口依据 ADR-029，尚未注册到 Core 地图目录或提供 CARLA 镜像。

### 高德地址定位

在高德开放平台申请 **Web 服务**类型 Key，确认拥有地理编码与 POI 搜索权限。
将 Key 配置为本机用户或系统环境变量 `AMAP_WEB_API_KEY`，或在本机 backend `.env` 中设置，
不要提交到仓库。Windows 启动器按进程、用户、系统的顺序读取，并传入 WSL 后端子进程。
配置改变后需重启生成服务；`GET /health` 的 `amap_configured` 和 `geocoder_provider`
可以确认是否启用，不返回 Key。

`MAP_GEOCODER_PROVIDER=auto` 默认有 Key 用高德，无 Key 用 Photon；也可显式设为 `amap`
或 `photon`。`MAP_AMAP_CITY` 可选填北京等城市约束。高德鉴权、权限、配额或网络错误会
直接显示，不会静默切换地点供应商。若地理编码已有有效候选，仅 POI 权限不足时保留候选并提示原因。
地点无匹配仍需补充说明，不能保证每个楼栋都已收录。

高德仅提供地点定位，周边道路仍取自 OSM。结果保留实际返回地址、匹配粒度、原 GCJ-02
坐标及近似 WGS84 转换说明，再供 Overpass 查询。该近似转换不代表测量级精度；地图需用户核对。
省市区县等粗粒度结果不能替代楼栋。Key 不写入来源记录、缓存、场景文件或错误信息。
真实地图预览使用红色定位针标注“原始位置”和匹配地点名称；针尖对应查询中心的实际坐标。
标记按照 SUMO 路网投影及偏移对齐，简化/详细预览及放大图片都保留，不代表车辆出生点。
未配置真实 Key 时只能完成离线协议和异常处理验证，不能宣称高德查询实测通过。

官方文档：[地理编码](https://developer.amap.com/api/webservice/guide/api/georegeo)、
[地点搜索](https://developer.amap.com/api/webservice/guide/api-advanced/search)、
[创建 Web 服务 Key](https://lbs.amap.com/api/webservice/create-project-and-key)。

## 场景工作界面（v1.2）

NL2MAP 场景工坊默认使用 **English**。右上角可随时切换为 **简体中文**，保留输入、当前草稿、
地图和未应用的参数修改；语言切换不会重新生成、发布或运行场景。英文图片中的地点、道路名
优先使用源 OSM 的 `name:en`，没有英文名称时使用拼音及英文道路/地点类型；中文图片保留原名。
英文名只用于显示，不改动源 OSM、SUMO 路网或地点坐标。用户原文和来源诊断仍保留原始内容。
已有真实地图的语言图片在读取时按当前规则更新，避免继续使用旧的中文标注缓存。
离线拼音转换依赖固定版本的 `pypinyin`，已包含在后端 requirements 和主项目可选依赖锁中。
Windows 启动 `scripts/dev/start_scene_workspace.ps1` 会直接进入英文简洁输入页；
使用 `-Language zh` 可显式以中文打开。普通流量模式和 L0–L5 自动驾驶模式均支持双语。

默认流程是：**独立“场景生成”页填写需求 → 生成预览 → 确认添加到场景 → 进入仿真配置 → 用户启动**。
生成不会试跑车辆；“拥堵”是目标场景设定，尚未运行时不提供实测车速/密度或拥堵达成结论。

本机 Windows 在项目根目录运行：

```powershell
./scripts/dev/start_scene_workspace.ps1
```

启动器复用已有 WSL roadgen/NL2MAP 环境、Windows 虚拟环境中的 SUMO 和桌面依赖。
生成服务使用 8001，Core API 使用 8000；API Key 仍读取环境变量 `OPENAI_API_KEY`。
进入工作区左侧“场景生成”，在居中的输入框填写“希望测试拥堵场景下车辆速度”，点击
“生成预览”或按回车。预览展示真实路网图片、目标交通状态、密度参考值、计划发车数、
时长、预热与种子。普通交通界面量化车辆总数和发车强度；自动驾驶界面额外显示 L0–L5
发车比例。计划发车总数不等于同时在网车辆数。待验证要求包含地图和车流两部分，是否适用由用户判断。
确认前仅保存工作区草稿，不出现在系统场景列表。图像加载失败可以重试；修改需求后须重新生成。
生成期间按钮显示“正在生成…”。地点未找到或服务失败时，输入框下方突出显示具体原因，
保留输入并提供“重试生成”；首次失败仍保持简洁入口，首次成功后才展开地图与设定。
点击“确认，添加到场景”才入库，随后可“进入仿真配置”。重复确认不会创建多个副本。
点击地图图片或“放大查看”可打开独立查看窗口，支持滚轮缩放、拖拽平移、原始尺寸与适应窗口。
场景设定支持直接输入车辆总数、目标密度、仿真时长、预热时间、随机种子、每入口车道小时流量、
总小时流量和密度推算参考速度；自动驾驶界面还可调整 L0–L5 百分比，合计必须为 100%。
时间以秒编辑，预热必须小于时长。点击“应用修改”统一更新车流 XML、发车计划与 SUMO 配置。
尚未应用或无效的编辑会阻止确认，失败后保留输入可重试或取消。修改复用路网，不调用模型、
不重新获取地图、不运行仿真。每次成功修改产生新草稿，原草稿及已添加场景不被覆盖。

数量、单车道流量、总流量、密度分为四种编排方式；编辑对应输入时明确切换驱动方式。
按密度编排使用密度乘参考速度，不再对用户显式值应用默认 10% 轻量系数；
按总小时流量编排按仿真时长向下取整，车辆总数须在 1–20,000 内。
修改时长或种子保留当前编排方式。自动驾驶百分比按最大余数分配整数车辆，
预览的实际车辆数、实际小时流量和实际比例是计算结果。目标密度与拥堵设定仍需运行后验证。

`POST /scenarios/{task_id}/revise` 支持上述字段的部分修改：`vehicle_count`、
`desired_density_veh_per_km_lane`、`duration_s`、`warmup_s`、`seed`、
`inflow_veh_per_hour_per_lane`、`total_flow_veh_per_hour`、`reference_speed_kmh`、
`automation_mix` 和 `demand_mode`（`count/inflow/density/total_flow`）。原数量单字段请求保持兼容；
未修改字段省略，不能传 `null`，多种驱动量同时出现时必须明确编排方式。
启动器默认打开普通交通模式；自动驾驶界面使用
`.venv/Scripts/python.exe -m trafficverse.cli ui --api-url http://127.0.0.1:8000`。

生成场景时长保持只读；需要改变时长时，修改文字后重新生成，避免仅改截止时间而
遗留原发车计划。车辆总数可在生成预览内直接编辑。点击“保存配置”仅建立快照；
点击“开始仿真”才走现有 Core 的正式运行。
独立模型需求不支持的要求会明确保留，生成成功不表示这些要求已实现。

确认后的场景以输入包保存到 `configs/maps/nl2-<任务ID>/`，系统动态发现，不需要重启。
`scenario.manifest.json` 保存目标/车流摘要和原始 intent/plan/默认来源；不复制试运行指标。
包标为 `scripted`，系统保留生成的路线和发车计划；运行只修改新建副本，源场景不被覆盖。
原生 SUMO 场景使用二维展示，暂不生成 CARLA 镜像。

也可以在项目根目录使用命令行（服务须启动）：

```powershell
.venv/Scripts/python.exe nl2map/scene_cli.py generate "希望测试拥堵场景下车辆速度"
.venv/Scripts/python.exe nl2map/scene_cli.py publish <生成响应中的任务ID>
.venv/Scripts/python.exe nl2map/scene_cli.py list
.venv/Scripts/python.exe nl2map/scene_cli.py show <任务ID>
```

新草稿及历史任务均需显式执行 `publish <任务ID>` 才导入，仅导入输入与设定。
`generate --publish` 显式直接添加，`--mode autonomy` 生成自动驾驶等级分配；默认模式为 `traffic`。
对应生成服务 API 为 `GET /scenarios`、`GET /scenarios/{task_id}`、
`POST /scenarios/{task_id}/publish`。桌面仍通过 Core `GET /api/v1/maps` 读取目录，
可选 `generated_scenario` 字段仅表达设定；启动沿用原有保存配置/prepare/start 接口。

修改数量使用 `POST /scenarios/{task_id}/revise`，请求如 `{"vehicle_count": 1200}`。
响应为新 `ScenarioResult` 草稿，含 `parent_task_id` 和 `manual_overrides`；原需求文字/引用
保留不变。新草稿仍需独立确认发布。已试运行的旧任务不能直接修改为未运行草稿。

## 场景生成接口与可选试运行

同一入口现在可以生成地图、车流和试运行结果：

```json
{ "description": "希望测试拥堵场景下车辆速度" }
```

`output_kind="auto"` 自动区分只生成地图与仿真目标；可显式选 `map` 或 `scenario`。
地图请求保留原行为；场景请求由模型抽取目标、交通状态、明确给出的数值参数和指标，
由程序补齐缺省值并生成 SUMO 路线/车辆，不让模型编造试运行结果。

- 没给地图：默认通过 roadgen 生成四向信号十字路口，每条支路约 300 米；默认值会记录。
- 给真实地点：沿用 OSM 路网流程，默认简化主干路，用户要求才细化。
- 复用已有地图：传 `map_task_id`。若文字要求“当前地图”却没传 ID，返回 `needs_clarification`，
  不猜测最近任务。场景在新工作区复制路网，不修改源图。
- `run_simulation=false`（默认）返回 `generated` 草稿，`metrics=null`。
  `publish_to_catalog=false`（默认）使 `catalog_map_id=null`，通过 `artifact_urls` 查看图片及输入。
  `POST /scenarios/{task_id}/publish` 确认添加后返回 `map_id`；显式 `publish_to_catalog=true`
  兼容旧脚本的直接入库行为。显式 `run_simulation=true` 保留一次试运行接口；桌面两个选项均固定 false。
- `simulation_mode="traffic"` 为普通交通；`autonomy` 未指定配比时默认 L0 100%并披露。
  完整的 L0–L5 百分比需求必须合计 100%，整数辆数采用最大余数分配，预览显示实际计划比例。
  例如“L0占70%，L3占30%”；数值与原文不符时返回具体原因。“各半”“其余”等模糊说法
  需改成明确百分比，或在预览中保留为待验证要求，不能静默当作已实现。
  等级沿用系统既有 SUMO 行为代理参数，不代表接入真实自动驾驶算法。

例如复用刚生成的图，明确发车数和时间：

```json
{
  "description": "使用当前地图测试车辆速度，计划发出500辆车，仿真5分钟，预热1分钟",
  "map_task_id": "20260101_000000_aabbcc",
  "run_simulation": false
}
```

示例 ID 需要替换成实际地图任务 ID。支持明确的总**计划发车数**、每小时每车道流量、
每公里每车道目标密度、仿真/预热时长、随机种子，以及速度/密度/排队/行程时间指标。
总发车数与每车道流量不能同时作为控制量。车型比例、天气、控制算法、自定义指标或
范围不明确的密度/流量会保留为 `unverified_requirements`，不自动声称实现。

缺省设置：

| 设置 | 缺省值及含义 |
|---|---|
| 仿真时长 / 预热 | 600 秒 / min(120 秒, 时长的 20%) |
| 种子 / 仿真步长 | 42 / 0.5 秒 |
| 低/中/拥堵密度提示 | 8 / 22 / 65 辆/公里/车道，工程场景参考值，并非实测 |
| 发车强度 | 数量、流量和密度均未指定时，按密度 × 参考速度 30 km/h 的 10% 使用轻量默认值；明确给出密度时按完整推算量，明确数量/流量时按用户要求 |
| 车辆 | 小客车，长 5 m、间距 2.5 m、tau=1 s、sigma=0.5；零速入网、由 SUMO 选择合适插入车道，沿用道路限速与信号程序 |
| 规模上限 | 最多 20000 辆计划发车、3600 秒；超限报错，不静默缩小 |

路线只使用实际编译网络中的小客车车道连接，排除掉头；优先边界入口/出口，最多选 12 个
有效入口，每入口最多 3 个可达出口。没有边界时选可达内部路线。以外部道路的平均车道长度
求最短路线，再优先选最远的可达终点；路线至少包含两条道路、总长至少 50 米。发车按入口
车道数加权均匀调度，种子控制入口抽样和路线选择；该分布不是校准的真实 OD。`demand` 公开实际选择范围、
路线数、计划发车量和全网车道公里。局部未覆盖路段仍计入全网密度分母。

每次保存原文引用 `intent`、完整参数 `plan`、默认来源 `defaults_used`、车流 `demand` 和实际结果 `metrics`。
默认车流可用 `SCENARIO_DEFAULT_DEMAND_SCALE` 配置，默认 `0.1`，设为 `1` 恢复原推算量。
例如北航18条选定入口车道、10分钟时，默认自由流72辆、普通198辆、拥堵目标585辆。
缩减只作用于新生成的未指定数值需求；实际形成何种交通状态仍由仿真验证。
极短的默认场景至少为每入口车道安排一辆车，避免缩减后变成空场景。
`intent.metrics/plan.metrics` 记录用户关注项；当前固定采集并返回下述全部指标，不作为输出字段筛选器：

- `mean_speed_kmh`：预热后每 0.5 秒 SUMO summary 的在网车辆数 × 时间加权速度，包含路口。
- `mean_external_density_veh_per_km_lane`：外部小客车车道的累计 `sampledSeconds` /
  观测时长 / 全部外部小客车车道公里；不含内部路口车道，未入网车辆不计入。
- 排队指标为预热后全网停驶车辆数的时间均值和最大值。
- 完成行程时间为整个运行中已入网且已到达车辆的 `tripinfo.duration` 算术均值，包含预热阶段；
  不含入网前等待，也不纳入未完成或未入网车辆，不能代表所有计划车辆的完整出行耗时。
- 计划发车、已入网、已到达、尚在网、未入网和末尾等待插入的车辆分别记录；
  末尾等待数与总未入网数可能不同（仍有未到发车步的车辆）。
- 拥堵的本地判据：相对限速的平均速度 ≤0.5，且停驶车辆占比 ≥20%，
  这样的时段占预热后时间 ≥30%。返回 `observed/not_observed/no_data/invalid_run`，
  是这次模型试运行的观察，不是普适定义或真实拥堵预测。碰撞/传送使测试标为无效。

`success` 表示运行和数据生成完成，并且请求拥堵时观察到了该判据；`needs_adjustment`
表示未观察到请求的拥堵、没有有效车辆数据或有碰撞/传送。不会通过降低限速、改信号或隐藏
入口积压来假装拥堵成功。其他交通状态暂不自动验证达成，`target_state=not_requested`，只提供观测。`user_review=pending`
保留用户判断是否符合研究用途。目标密度也不是初始/持续密度控制器，不保证等于实际密度。

生成工作区包含 `network.net.xml`、`traffic.rou.xml`、`scenario.sumocfg`、`metrics.add.xml`、
`scenario.json`、地图预览和检查记录。只有显式试运行后才生成
`summary.xml/lane_metrics.xml/tripinfo.xml/statistics.xml`、`metrics.json` 和速度/排队 `timeseries.csv`。
进入输出目录执行 `sumo -c scenario.sumocfg` 可复现；手动重新运行会覆盖该目录的原生观测文件，
Python 派生的 `metrics.json` 需重新调用采集逻辑，建议 API 新建场景以保留旧结果。

v0.5 已接入 Core 原生 SUMO 场景目录与桌面手动启动；显式试运行仍是独立 SUMO CLI。
没有 CARLA 镜像或新控制器。SUMO 字段含义参考
[Summary](https://sumo.dlr.de/docs/Simulation/Output/Summary.html) 和
[Lane/Edge Measures](https://sumo.dlr.de/docs/Simulation/Output/Lane-_or_Edge-based_Traffic_Measures.html)。

## V2 计划（保留）

- 定义项目自己的受约束 RoadSpec JSON，并将自然语言解析和地图编译分离。
- 基于 RoadSpec 生成 OpenDRIVE，再按需求生成 SUMO 和 CARLA 所需的同源地图资产；
  联仿前验证车道、坐标配准和信号灯绑定。
