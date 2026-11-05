"""Approximate mainland GCJ-02 normalization for OSM lookup, not survey-grade coordinates.

Uses the common public Krasovsky polynomial model and a bounded numerical inverse.
No provider promises this inverse or a real-world error bound. Callers retain native
coordinates and label this approximation separately from provider address precision.
"""

import math


def _forward(longitude: float, latitude: float) -> tuple[float, float]:
    x, y = longitude - 105.0, latitude - 35.0
    shared = 40 / 3 * (math.sin(6 * x * math.pi) + math.sin(2 * x * math.pi))
    latitude_offset = (
        -100
        + 2 * x
        + 3 * y
        + 0.2 * y * y
        + 0.1 * x * y
        + 0.2 * math.sqrt(abs(x))
        + shared
        + 40 / 3 * math.sin(y * math.pi)
        + 80 / 3 * math.sin(y * math.pi / 3)
        + 320 / 3 * math.sin(y * math.pi / 12)
        + 640 / 3 * math.sin(y * math.pi / 30)
    )
    longitude_offset = (
        300
        + x
        + 2 * y
        + 0.1 * x * x
        + 0.1 * x * y
        + 0.1 * math.sqrt(abs(x))
        + shared
        + 40 / 3 * math.sin(x * math.pi)
        + 80 / 3 * math.sin(x * math.pi / 3)
        + 100 * math.sin(x * math.pi / 12)
        + 200 * math.sin(x * math.pi / 30)
    )
    axis, eccentricity = 6378245.0, 0.006693421622965943
    radians = math.radians(latitude)
    magic = 1 - eccentricity * math.sin(radians) ** 2
    longitude_offset *= 180 * math.sqrt(magic) / (axis * math.cos(radians) * math.pi)
    latitude_offset *= 180 * magic**1.5 / (axis * (1 - eccentricity) * math.pi)
    return longitude + longitude_offset, latitude + latitude_offset


def gcj02_to_wgs84(longitude: float, latitude: float) -> tuple[float, float]:
    """Invert the approximate offset model within its mainland working bounds."""
    if not (math.isfinite(longitude) and math.isfinite(latitude)) or not (
        73.66 <= longitude <= 135.05 and 3.86 <= latitude <= 53.55
    ):
        raise ValueError("高德候选坐标超出当前大陆坐标转换支持范围，请改用 Photon 或其他地点")
    lon, lat = longitude, latitude
    for _ in range(12):
        shifted_lon, shifted_lat = _forward(lon, lat)
        dx, dy = shifted_lon - longitude, shifted_lat - latitude
        lon, lat = lon - dx, lat - dy
        if max(abs(dx), abs(dy)) < 1e-9:
            return lon, lat
    raise ValueError("高德候选坐标转换未收敛，请重新确认地点")
