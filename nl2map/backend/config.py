"""配置读取：所有敏感信息都从 .env 文件读取，不写死在代码里。"""

import os
from pathlib import Path

from dotenv import load_dotenv
from realmap_models import RealMapSettings
from scenario_models import ScenarioSettings

load_dotenv()

MODEL_BASE_URL = os.getenv("MODEL_BASE_URL", "").strip()
MODEL_API_KEY = os.getenv("MODEL_API_KEY", "").strip()
MODEL_NAME = os.getenv("MODEL_NAME", "").strip()

# 模型是否已配置
MODEL_CONFIGURED = bool(MODEL_BASE_URL and MODEL_API_KEY and MODEL_NAME)
AMAP_WEB_API_KEY = os.getenv("AMAP_WEB_API_KEY", "").strip()

# 每次生成代码的执行超时时间（秒）
EXEC_TIMEOUT_SEC = 60
MAX_GENERATION_ATTEMPTS = 3

# Cardinal labels are unavailable for approaches farther from an axis than this angle.
XML_CARDINAL_MAX_DEVIATION_DEG = 30.0

# 工作目录：每次请求生成的代码和路网文件都放这里
WORKSPACES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workspaces")
SCENARIO_CATALOG_DIR = Path(__file__).resolve().parents[2] / "configs" / "maps"

# Local real-map demo settings, validated at startup; never contains API credentials.
REAL_MAP = RealMapSettings(
    geocoder_provider=os.getenv("MAP_GEOCODER_PROVIDER", "").strip() or "auto",
    amap_city=os.getenv("MAP_AMAP_CITY", "").strip(),
    photon_url=os.getenv("MAP_PHOTON_URL", "https://photon.komoot.io/api/"),
    overpass_url=os.getenv("MAP_OVERPASS_URL", "https://overpass-api.de/api/interpreter"),
    user_agent=os.getenv("MAP_USER_AGENT", "SimHarness-NL2MAP/0.3 (local research demo)"),
    default_radius_m=float(os.getenv("MAP_DEFAULT_RADIUS_M", "1000")),
    max_radius_m=float(os.getenv("MAP_MAX_RADIUS_M", "3000")),
    request_timeout_sec=int(os.getenv("MAP_REQUEST_TIMEOUT_SEC", "75")),
    cache_ttl_sec=int(os.getenv("MAP_CACHE_TTL_SEC", "86400")),
    max_response_bytes=int(os.getenv("MAP_MAX_RESPONSE_BYTES", str(32 * 1024 * 1024))),
    tool_timeout_sec=int(os.getenv("MAP_TOOL_TIMEOUT_SEC", "90")),
)

SCENARIO = ScenarioSettings(
    default_duration_s=int(os.getenv("SCENARIO_DURATION_S", "600")),
    default_seed=int(os.getenv("SCENARIO_SEED", "42")),
    default_demand_scale=float(os.getenv("SCENARIO_DEFAULT_DEMAND_SCALE", "0.1")),
    run_timeout_s=int(os.getenv("SCENARIO_RUN_TIMEOUT_S", "120")),
)
