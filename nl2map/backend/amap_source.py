"""Bounded Amap address/POI lookup with explicit CRS and credential-safe evidence."""

import hashlib
import json
import re
import threading
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import config
from china_coordinates import gcj02_to_wgs84
from realmap_models import GeocodingResult, PlaceCandidate, SourceRecord

_BASE_URL = "https://restapi.amap.com/v3/"
_REQUEST_LOCK = threading.Lock()
_DETAIL_LEVELS = {
    "道路交叉路口",
    "兴趣点",
    "门牌号",
    "单元号",
    "楼层",
    "房间",
    "公交地铁站点",
    "门址",
    "住宅区",
}
_COORDINATE_NOTE = (
    "地点来源为高德，原始坐标为 GCJ-02；用于 OSM 查询的 WGS84 坐标由本地近似逆变换得到，"
    "转换收敛不代表实地定位精度，需结合预览确认位置。"
)
_ERROR_HINTS = {
    "10001": "Key 无效，请配置有效的高德 Web 服务 Key",
    "10002": "接口权限不足，请在高德控制台开通地理编码和 POI 搜索权限",
    "10003": "调用次数已达日限额，请稍后重试或调整配额",
    "10004": "访问频率超限，请稍后重试",
    "10005": "IP 白名单校验失败，请检查高德控制台的服务 IP 配置",
    "10006": "域名校验失败，请检查 Key 的服务平台与白名单",
    "10007": "数字签名校验失败，请检查 Key 的校验方式",
    "10009": "Key 与 Web 服务平台不匹配，请申请 Web 服务类型 Key",
    "10010": "IP 访问频率超限，请稍后重试",
    "10012": "接口权限不足，请检查高德控制台的服务权限",
    "10013": "Key 已被删除，请重新配置",
    "10014": "账户调用量超限，请检查高德控制台的配额",
    "10019": "服务调用量超限，请检查高德控制台的配额",
    "10044": "账号服务能力未开通或已过期，请检查高德控制台",
}


class AmapRequestError(ValueError):
    """A sanitized service error with a machine-readable status code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _text(value: object) -> str:
    # Amap represents absent strings as [] in otherwise successful JSON responses.
    return value.strip() if isinstance(value, str) else ""


def _building_numbers(text: str) -> set[str]:
    numbers = re.findall(
        r"([0-9]+|[一二三四五六七八九十百零〇两]+)\s*(?:号楼|栋|幢|座)",
        unicodedata.normalize("NFKC", text),
    )
    singles = dict(zip("一二三四五六七八九", "123456789", strict=True))
    singles["两"] = "2"
    return {
        str(int(number)) if number.isdigit() else singles.get(number, number) for number in numbers
    }


def _request(
    endpoint: str, parameters: dict[str, str], output: Path
) -> tuple[dict[str, Any], SourceRecord]:
    """Only the outbound request contains the key; errors and persisted data never do."""
    key = config.AMAP_WEB_API_KEY
    if not key:
        raise ValueError("未配置高德 AMAP_WEB_API_KEY，请设置 Web 服务 Key 后重启场景生成服务")
    safe_query = urlencode(parameters)
    url = _BASE_URL + endpoint
    request = Request(
        url + "?" + urlencode({**parameters, "key": key}),
        headers={"User-Agent": config.REAL_MAP.user_agent},
    )
    try:
        with (
            _REQUEST_LOCK,
            urlopen(request, timeout=config.REAL_MAP.request_timeout_sec) as response,
        ):
            body = response.read(config.REAL_MAP.max_response_bytes + 1)
    except HTTPError as exc:
        raise ValueError(f"高德地点查询 HTTP {exc.code}，请检查权限、配额或稍后重试") from None
    except (URLError, TimeoutError, OSError):
        # Exception reasons may embed a URL with its credential.
        raise ValueError("高德地点服务连接失败，请检查网络后重试") from None
    if len(body) > config.REAL_MAP.max_response_bytes:
        raise ValueError("高德地点响应超过大小上限，请缩短查询或稍后重试")
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeError):
        raise ValueError("高德地点服务返回无效 JSON，请稍后重试") from None
    if not isinstance(payload, dict):
        raise ValueError("高德地点服务返回格式无效，请稍后重试")
    if payload.get("status") != "1":
        raw_code = payload.get("infocode")
        code = (
            raw_code
            if isinstance(raw_code, str) and raw_code.isdigit() and len(raw_code) == 5
            else "unknown"
        )
        hint = _ERROR_HINTS.get(code, "请检查高德控制台的接口权限、配额和请求参数")
        raise AmapRequestError(code, f"高德地点查询失败（{code}）：{hint}")
    # Defense in depth if a provider/proxy echoes a credential inside successful data.
    sanitized = json.dumps(payload, ensure_ascii=False, indent=2).replace(key, "[redacted]")
    payload = json.loads(sanitized)
    output.write_text(sanitized + "\n", encoding="utf-8")
    return payload, SourceRecord(
        url=url,
        query=safe_query,
        fetched_at=datetime.now(timezone.utc).isoformat(),
        cache_hit=False,
    )


def _candidate(item: dict[str, Any], *, poi: bool) -> PlaceCandidate | None:
    name = _text(item.get("name" if poi else "formatted_address"))
    level = "兴趣点" if poi else _text(item.get("level"))
    if not name or level not in _DETAIL_LEVELS:
        return None
    address = _text(item.get("address" if poi else "formatted_address"))
    city = _text(item.get("cityname" if poi else "city"))
    district = _text(item.get("adname" if poi else "district"))
    province = _text(item.get("pname" if poi else "province"))
    if any(region in province + city for region in ("香港", "澳门", "台湾")):
        return None
    try:
        location = _text(item.get("location")).split(",")
        if len(location) != 2:
            return None
        native_lon, native_lat = map(float, location)
        longitude, latitude = gcj02_to_wgs84(native_lon, native_lat)
    except ValueError:
        return None
    identity = json.dumps(
        ["poi" if poi else "geocode", _text(item.get("id")), name, native_lon, native_lat],
        ensure_ascii=False,
    )
    return PlaceCandidate(
        id="amap:" + hashlib.sha256(identity.encode()).hexdigest()[:32],
        name=name,
        longitude=longitude,
        latitude=latitude,
        city=city,
        district=district,
        country="中国",
        street=_text(item.get("street")),
        provider="amap",
        source_crs="GCJ-02",
        source_longitude=native_lon,
        source_latitude=native_lat,
        formatted_address=address,
        match_level=level,
        coordinate_note=_COORDINATE_NOTE,
    )


def geocode(place_query: str, alternate_query: str, output: Path) -> GeocodingResult:
    """Try at most two names and two endpoints; never invent or silently broaden a name."""
    if not config.AMAP_WEB_API_KEY:
        raise ValueError("未配置高德 AMAP_WEB_API_KEY，请设置 Web 服务 Key 后重启场景生成服务")
    queries = list(dict.fromkeys(q.strip() for q in (place_query, alternate_query) if q.strip()))
    if not queries:
        raise ValueError("请填写需要定位的地点名称")
    candidates: dict[str, PlaceCandidate] = {}
    requested_buildings = _building_numbers(place_query)
    records: list[SourceRecord] = []
    warnings: list[str] = []
    poi_available = True
    for index, query in enumerate(queries, 1):
        for kind, endpoint, parameters, field in (
            ("geocode", "geocode/geo", {"address": query, "output": "JSON"}, "geocodes"),
            (
                "poi",
                "place/text",
                {
                    "keywords": query,
                    "offset": "5",
                    "page": "1",
                    "extensions": "base",
                    "output": "JSON",
                },
                "pois",
            ),
        ):
            if kind == "poi" and not poi_available:
                continue
            if config.REAL_MAP.amap_city:
                parameters["city"] = config.REAL_MAP.amap_city
                if kind == "poi":
                    parameters["citylimit"] = "true"
            try:
                payload, record = _request(
                    endpoint, parameters, output / f"amap-{kind}-{index}.json"
                )
            except AmapRequestError as exc:
                if kind != "poi" or not candidates or exc.code not in {"10002", "10012", "10044"}:
                    raise
                warning = "高德 POI 搜索权限未开通，保留本次高德地址解析候选；" + str(exc)
                warnings.append(warning)
                (output / f"amap-poi-{index}-error.json").write_text(
                    json.dumps({"code": exc.code, "error": warning}, ensure_ascii=False),
                    encoding="utf-8",
                )
                poi_available = False
                continue
            records.append(record)
            items = payload.get(field)
            if not isinstance(items, list):
                raise ValueError("高德地点候选格式无效，请稍后重试")
            for item in items[:5]:
                if not isinstance(item, dict):
                    raise ValueError("高德地点候选格式无效，请稍后重试")
                candidate = _candidate(item, poi=kind == "poi")
                if candidate is None:
                    warnings.append(
                        "部分高德候选仅定位到区域/道路、缺少有效坐标或超出大陆转换范围，未用作场景中心。"
                    )
                else:
                    if not requested_buildings.issubset(
                        _building_numbers(candidate.name + " " + candidate.formatted_address)
                    ):
                        warnings.append(
                            "已过滤未匹配请求具体楼栋标识的高德候选，不将小区或道路中心作为替代。"
                        )
                        continue
                    candidates.setdefault(candidate.id, candidate)
    if candidates:
        warnings.append(_COORDINATE_NOTE)
    return GeocodingResult(list(candidates.values()), records, list(dict.fromkeys(warnings)))
