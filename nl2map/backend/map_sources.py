"""Bounded, cached Photon/Overpass requests. Provider content is data, never code."""

import json
import threading
import time
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import amap_source
import config
from pydantic import BaseModel, TypeAdapter
from realmap_models import GeocodingResult, PlaceCandidate, SourceRecord

_provider_lock = threading.Lock()


class _CacheEntry(BaseModel):
    filename: str
    fetched_at: str


@dataclass
class SourceResponse:
    body: bytes
    record: SourceRecord


def _fetch(url: str, query: str, *, post: bool) -> SourceResponse:
    """Serialize public-provider requests, reuse cached data, never retry rate limits."""
    settings = config.REAL_MAP
    cache = Path(config.WORKSPACES_DIR) / "_map_cache"
    cache.mkdir(parents=True, exist_ok=True)
    index_path = cache / "index.json"
    key = json.dumps([url, query, post])
    with _provider_lock:
        index: dict[str, _CacheEntry] = {}
        if index_path.exists():
            index = TypeAdapter(dict[str, _CacheEntry]).validate_json(index_path.read_text("utf-8"))
        entry = index.get(key)
        if entry:
            age = (
                datetime.now(timezone.utc) - datetime.fromisoformat(entry.fetched_at)
            ).total_seconds()
            stored = cache / entry.filename
            if 0 <= age < settings.cache_ttl_sec and stored.is_file():
                return SourceResponse(
                    stored.read_bytes(),
                    SourceRecord(url=url, query=query, fetched_at=entry.fetched_at, cache_hit=True),
                )
        # Single local service process; public providers must not be used for bulk lookup.
        time.sleep(1)
        payload = urlencode({"data": query}).encode() if post else None
        target = url if post else url + ("&" if "?" in url else "?") + query
        request = Request(target, data=payload, headers={"User-Agent": settings.user_agent})
        try:
            with urlopen(request, timeout=settings.request_timeout_sec) as response:
                body = response.read(settings.max_response_bytes + 1)
        except HTTPError as exc:
            raise ValueError(
                f"地图服务 HTTP {exc.code}；没有自动重试。请稍后重试或配置自建服务。"
            ) from exc
        except (URLError, TimeoutError) as exc:
            raise ValueError(f"地图服务不可用：{exc}；请检查网络或配置地图服务地址") from exc
        if len(body) > settings.max_response_bytes:
            raise ValueError("地图响应超过大小上限，请缩小范围")
        if post:
            root = ET.fromstring(body)
            if root.tag != "osm" or root.find("remark") is not None:
                raise ValueError("Overpass 未返回完整 OSM 数据，请稍后重试或缩小范围")
        else:
            document = json.loads(body)
            if document.get("type") != "FeatureCollection" or not isinstance(
                document.get("features"), list
            ):
                raise ValueError("地名服务返回格式无效")
        fetched_at = datetime.now(timezone.utc).isoformat()
        filename = uuid.uuid4().hex + (".osm.xml" if post else ".json")
        (cache / filename).write_bytes(body)
        index[key] = _CacheEntry(filename=filename, fetched_at=fetched_at)
        index_path.write_text(
            json.dumps({k: v.model_dump() for k, v in index.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return SourceResponse(
            body, SourceRecord(url=url, query=query, fetched_at=fetched_at, cache_hit=False)
        )


def geocoder_provider() -> str:
    """Resolve the configured provider without exposing its credential."""
    provider = config.REAL_MAP.geocoder_provider
    if provider == "auto":
        return "amap" if config.AMAP_WEB_API_KEY else "photon"
    return provider


def geocode(place_query: str, alternate_query: str, output: Path) -> GeocodingResult:
    """Use the configured provider; provider errors are never silent fallbacks."""
    if geocoder_provider() == "amap":
        return amap_source.geocode(place_query, alternate_query, output)
    return _photon_geocode(place_query, alternate_query, output)


def _photon_geocode(place_query: str, alternate_query: str, output: Path) -> GeocodingResult:
    """Merge at most two queries; unrelated first-query hits must not block an alternate."""
    candidates: dict[str, PlaceCandidate] = {}
    records = []
    warnings = []
    queries = list(dict.fromkeys(q.strip() for q in [place_query, alternate_query] if q.strip()))
    for number, query in enumerate(queries, 1):
        try:
            response = _fetch(
                config.REAL_MAP.photon_url, urlencode({"q": query, "limit": 5}), post=False
            )
        except (ValueError, OSError) as exc:
            if number == 1 or not candidates:
                raise
            warning = f"备用地名查询“{query}”未完成，保留首轮候选。原因：{exc}"
            warnings.append(warning)
            (output / f"geocoder-{number}-error.json").write_text(
                json.dumps({"query": query, "error": str(exc)}, ensure_ascii=False),
                encoding="utf-8",
            )
            continue
        records.append(response.record)
        (output / f"geocoder-{number}.json").write_bytes(response.body)
        for feature in json.loads(response.body)["features"][:5]:
            prop = feature.get("properties", {})
            coordinates = feature.get("geometry", {}).get("coordinates", [])
            osm_type, osm_id = prop.get("osm_type"), prop.get("osm_id")
            if (
                feature.get("geometry", {}).get("type") != "Point"
                or len(coordinates) != 2
                or osm_type not in {"N", "W", "R"}
                or not isinstance(osm_id, int)
                or not prop.get("name")
            ):
                continue
            candidate = PlaceCandidate(
                id=f"{osm_type}{osm_id}",
                name=prop["name"],
                longitude=coordinates[0],
                latitude=coordinates[1],
                **{
                    k: prop.get(k, "")
                    for k in ("city", "district", "country", "street", "osm_key", "osm_value")
                },
            )
            candidates.setdefault(candidate.id, candidate)
    return GeocodingResult(list(candidates.values()), records, warnings)


def fetch_roads(place: PlaceCandidate, radius_m: float, output: Path) -> SourceRecord:
    """Select nearby ways and restriction relations, preserving whole OSM ways."""
    if not 100 <= radius_m <= config.REAL_MAP.max_radius_m:
        raise ValueError(f"半径须为 100–{config.REAL_MAP.max_radius_m:g} 米，未自动缩放")
    query = (
        "[out:xml][timeout:50][maxsize:33554432];\n"
        f'way(around:{radius_m:g},{place.latitude:.7f},{place.longitude:.7f})["highway"]->.roads;\n'
        '(.roads; rel(bw.roads)["type"="restriction"];)->.selected;\n'
        "(.selected; >;); out body;\n"
    )
    (output / "overpass-query.txt").write_text(query, encoding="utf-8")
    response = _fetch(config.REAL_MAP.overpass_url, query, post=True)
    (output / "source.osm.xml").write_bytes(response.body)
    root = ET.fromstring(response.body)
    if not any(way.find("tag[@k='highway']") is not None for way in root.findall("way")):
        raise ValueError("指定范围没有可用 OSM 道路，请核对地点或扩大范围")
    return response.record
