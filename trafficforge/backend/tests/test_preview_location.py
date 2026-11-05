"""Check geographic marker projection independently of image placement."""

import math
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from preview_location import MapOriginMarker, project_origin

UTM_50_NORTH = "+proj=utm +zone=50 +datum=WGS84 +units=m +no_defs"


def network(projection: str = UTM_50_NORTH, offset: str = "0,0") -> ET.Element:
    root = ET.Element("net")
    ET.SubElement(root, "location", projParameter=projection, netOffset=offset)
    return root


class OriginProjectionTest(unittest.TestCase):
    def test_central_meridian_control_point_uses_longitude_first_and_adds_offset(self) -> None:
        # UTM zone 50 has a 117° central meridian and 500,000 m false easting.
        # Its northern equator is 0 m northing; expected values need no pyproj call.
        point = project_origin(
            network(offset="-499876.5,42.25"), MapOriginMarker("control", 117.0, 0.0)
        )
        self.assertAlmostEqual(point[0], 123.5, places=6)
        self.assertAlmostEqual(point[1], 42.25, places=6)

    def test_southern_hemisphere_uses_projection_metadata(self) -> None:
        point = project_origin(
            network(UTM_50_NORTH + " +south", "-500000,-9999875"),
            MapOriginMarker("southern control", 117.0, 0.0),
        )
        self.assertAlmostEqual(point[0], 0, places=6)
        self.assertAlmostEqual(point[1], 125, places=6)

    def test_missing_or_unknown_projection_never_invents_a_center(self) -> None:
        with self.assertRaisesRegex(ValueError, "投影"):
            project_origin(ET.Element("net"), MapOriginMarker("control", 117, 0))
        for projection in ("", "!", "-", ".", "invalid-projection"):
            with self.subTest(projection=projection), self.assertRaisesRegex(ValueError, "投影"):
                project_origin(network(projection), MapOriginMarker("control", 117, 0))

    def test_non_metric_or_unprojected_network_is_rejected(self) -> None:
        for projection in ("EPSG:4326", UTM_50_NORTH.replace("+units=m", "+units=ft")):
            with self.subTest(projection=projection), self.assertRaisesRegex(ValueError, "米制"):
                project_origin(network(projection), MapOriginMarker("control", 117, 0))

    def test_bad_network_offset_is_rejected(self) -> None:
        for offset in ("", "1", "1,2,3", "1,nan", "inf,1", "bad,0"):
            with self.subTest(offset=offset), self.assertRaisesRegex(ValueError, "偏移"):
                project_origin(network(offset=offset), MapOriginMarker("control", 117, 0))

    def test_nonfinite_or_out_of_range_wgs84_coordinates_are_rejected(self) -> None:
        for longitude, latitude in ((math.nan, 0), (117, math.inf), (181, 0), (117, 91)):
            with (
                self.subTest(longitude=longitude, latitude=latitude),
                self.assertRaisesRegex(ValueError, "坐标"),
            ):
                project_origin(network(), MapOriginMarker("control", longitude, latitude))


if __name__ == "__main__":
    unittest.main()
