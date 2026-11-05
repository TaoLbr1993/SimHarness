"""Project the requested WGS84 location into the compiled SUMO network frame."""

import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError


@dataclass(frozen=True, slots=True)
class MapOriginMarker:
    """Describe a place using WGS84 coordinates, never provider-native GCJ-02 values."""

    name: str
    longitude: float
    latitude: float


def _network_offset(location: ET.Element) -> tuple[float, float]:
    try:
        x, y = (float(value) for value in location.get("netOffset", "").split(","))
    except ValueError as exc:
        raise ValueError("路网缺少有效的坐标偏移，无法定位原始位置标记。") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("路网坐标偏移不是有限数值，无法定位原始位置标记。")
    return x, y


def project_origin(root: ET.Element, origin: MapOriginMarker) -> tuple[float, float]:
    """Return metric SUMO XY by applying the network projection and then netOffset."""
    if not (
        math.isfinite(origin.longitude)
        and math.isfinite(origin.latitude)
        and -180 <= origin.longitude <= 180
        and -90 <= origin.latitude <= 90
    ):
        raise ValueError("原始位置的 WGS84 坐标无效，无法绘制位置标记。")
    location = root.find("location")
    if location is None:
        raise ValueError("路网缺少地理投影信息，无法定位原始位置标记。")
    projection = location.get("projParameter", "").strip()
    if projection in {"", "!", "-", "."}:
        raise ValueError("路网没有可用的地理投影，无法定位原始位置标记。")
    offset_x, offset_y = _network_offset(location)
    try:
        crs = CRS.from_user_input(projection)
    except ProjError as exc:
        raise ValueError("路网地理投影无效，无法定位原始位置标记。") from exc
    if (
        not crs.is_projected
        or len(crs.axis_info) < 2
        or any(
            not math.isclose(axis.unit_conversion_factor, 1.0, rel_tol=1e-12)
            for axis in crs.axis_info[:2]
        )
    ):
        raise ValueError("路网不是米制平面投影，无法定位原始位置标记。")
    try:
        # SUMO applies PROJ before adding netOffset. always_xy preserves lon/lat order.
        transform = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
        x, y = transform.transform(origin.longitude, origin.latitude, errcheck=True)
        result = float(x) + offset_x, float(y) + offset_y
    except (ProjError, ValueError, TypeError) as exc:
        raise ValueError("原始位置无法转换到路网投影，不能绘制位置标记。") from exc
    if not all(math.isfinite(value) for value in result):
        raise ValueError("原始位置投影后的坐标无效，不能绘制位置标记。")
    return result
