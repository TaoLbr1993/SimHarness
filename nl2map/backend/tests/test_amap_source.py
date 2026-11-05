"""Offline Amap contract, coordinate and credential-protection checks."""

import json
import os
import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import amap_source
import config
import map_sources
from china_coordinates import gcj02_to_wgs84


class AmapTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.secret = "fake-key-for-offline-tests"
        for name, value in (("WORKSPACES_DIR", self.temp.name), ("AMAP_WEB_API_KEY", self.secret)):
            item = patch.object(config, name, value)
            item.start()
            self.addCleanup(item.stop)
        item = patch.object(
            config, "REAL_MAP", config.REAL_MAP.model_copy(update={"geocoder_provider": "amap"})
        )
        item.start()
        self.addCleanup(item.stop)

    def test_known_beijing_pair_is_not_used_as_unshifted_osm_coordinate(self):
        # Independent frozen reference pair for the widely used Krasovsky polynomial approximation.
        lon, lat = gcj02_to_wgs84(116.40337249402477, 39.91793074924595)
        self.assertAlmostEqual(lon, 116.397128, places=6)
        self.assertAlmostEqual(lat, 39.916527, places=6)
        for point in ((0, 0), (float("nan"), 40), (181, 40)):
            with self.assertRaises(ValueError):
                gcj02_to_wgs84(*point)

    def responses(self):
        return [
            {
                "status": "1",
                "geocodes": [
                    {
                        "formatted_address": "北京市朝阳区测试园2号楼",
                        "level": "门牌号",
                        "city": "北京市",
                        "district": "朝阳区",
                        "location": "116.40337249402477,39.91793074924595",
                    }
                ],
            },
            {
                "status": "1",
                "pois": [
                    {
                        "id": "BTEST001",
                        "name": "测试园2号楼",
                        "address": "测试路8号",
                        "cityname": "北京市",
                        "adname": "朝阳区",
                        "location": "116.40337249402477,39.91793074924595",
                    }
                ],
            },
        ]

    def test_actual_provider_evidence_and_sanitized_request_records_are_preserved(self):
        with patch.object(amap_source, "urlopen") as remote:
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(p).encode() for p in self.responses()
            ]
            result = map_sources.geocode("北京市朝阳区测试园2号楼", "", self.root)
        self.assertEqual(len(result.candidates), 2)
        candidate = result.candidates[0]
        self.assertEqual(candidate.provider, "amap")
        self.assertEqual(candidate.source_crs, "GCJ-02")
        self.assertEqual(candidate.match_level, "门牌号")
        self.assertEqual(candidate.formatted_address, "北京市朝阳区测试园2号楼")
        self.assertAlmostEqual(candidate.longitude, 116.397128, places=6)
        self.assertNotEqual(candidate.longitude, candidate.source_longitude)
        self.assertIn("近似", candidate.coordinate_note)
        self.assertRegex(candidate.id, r"^amap:[0-9a-f]{32}$")
        request_url = remote.call_args_list[0].args[0].full_url
        self.assertEqual(parse_qs(urlparse(request_url).query)["key"], [self.secret])
        self.assertNotIn(self.secret, repr(result))
        self.assertTrue(result.records)
        for record in result.records:
            self.assertNotIn("key=", record.query)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret, path.read_text(encoding="utf-8"))

    def test_coarse_geocode_does_not_impersonate_building_and_native_name_is_used(self):
        payloads = [
            {
                "status": "1",
                "geocodes": [
                    {"formatted_address": "北京市朝阳区", "level": "区县", "location": "116.4,39.9"}
                ],
            },
            {
                "status": "1",
                "pois": [
                    {
                        "id": "TEST",
                        "name": "另一个小区",
                        "address": [],
                        "cityname": [],
                        "adname": [],
                        "location": "116.4,39.9",
                    }
                ],
            },
        ]
        with patch.object(amap_source, "urlopen") as remote:
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(p).encode() for p in payloads
            ]
            result = map_sources.geocode("要求的楼栋", "", self.root)
        self.assertEqual([c.name for c in result.candidates], ["另一个小区"])
        self.assertTrue(result.warnings)

    def test_provider_auth_quota_network_errors_never_leak_keys_or_fallback(self):
        for code in ("10001", "10003", "10009", "10012", "10044"):
            with (
                patch.object(amap_source, "urlopen") as remote,
                patch.object(map_sources, "_fetch") as photon,
            ):
                remote.return_value.__enter__.return_value.read.return_value = json.dumps(
                    {"status": "0", "infocode": code, "info": self.secret}
                ).encode()
                with self.assertRaises(ValueError) as error:
                    map_sources.geocode("地址", "备用", self.root)
                self.assertIn(code, str(error.exception))
                self.assertNotIn(self.secret, str(error.exception))
                self.assertEqual(remote.call_count, 1)
                photon.assert_not_called()
        for error in (
            URLError(self.secret),
            HTTPError("https://restapi.amap.com/?key=" + self.secret, 403, self.secret, {}, None),
        ):
            with (
                patch.object(amap_source, "urlopen", side_effect=error),
                self.assertRaises(ValueError) as caught,
            ):
                map_sources.geocode("地址", "", self.root)
            self.assertNotIn(self.secret, str(caught.exception))

    def test_missing_explicit_key_fails_but_auto_without_key_uses_photon(self):
        with (
            patch.object(config, "AMAP_WEB_API_KEY", ""),
            patch.object(amap_source, "urlopen") as remote,
        ):
            with self.assertRaisesRegex(ValueError, "AMAP_WEB_API_KEY"):
                map_sources.geocode("地址", "", self.root)
            remote.assert_not_called()
        with (
            patch.object(config, "AMAP_WEB_API_KEY", ""),
            patch.object(
                config, "REAL_MAP", config.REAL_MAP.model_copy(update={"geocoder_provider": "auto"})
            ),
        ):
            self.assertEqual(map_sources.geocoder_provider(), "photon")

    def test_empty_provider_environment_uses_auto_instead_of_preventing_startup(self):
        with patch.dict(os.environ, {"MAP_GEOCODER_PROVIDER": "  "}):
            loaded = runpy.run_path(config.__file__)
        self.assertEqual(loaded["REAL_MAP"].geocoder_provider, "auto")

    def test_malformed_and_oversized_payloads_are_actionable(self):
        payloads = (
            b"not-json",
            b"[]",
            b'{"status":"1","geocodes":"bad"}',
            b"x" * (config.REAL_MAP.max_response_bytes + 1),
        )
        for payload in payloads:
            with patch.object(amap_source, "urlopen") as remote, self.assertRaises(ValueError):
                remote.return_value.__enter__.return_value.read.return_value = payload
                map_sources.geocode("地址", "", self.root)

    def test_city_filter_unique_queries_and_candidate_limit(self):
        settings = config.REAL_MAP.model_copy(update={"amap_city": "110105"})
        payloads = self.responses()
        payloads[1]["pois"] = [{**payloads[1]["pois"][0], "id": str(index)} for index in range(12)]
        with (
            patch.object(config, "REAL_MAP", settings),
            patch.object(amap_source, "urlopen") as remote,
        ):
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(payload).encode() for payload in payloads
            ]
            result = map_sources.geocode(" 地址 ", "地址", self.root)
        self.assertEqual(remote.call_count, 2)
        self.assertEqual(len(result.candidates), 6)
        geo = parse_qs(urlparse(remote.call_args_list[0].args[0].full_url).query)
        poi = parse_qs(urlparse(remote.call_args_list[1].args[0].full_url).query)
        self.assertEqual(geo["city"], ["110105"])
        self.assertEqual(poi["citylimit"], ["true"])

    def test_successful_response_echo_does_not_persist_credentials(self):
        payloads = self.responses()
        payloads[0]["debug"] = "key=" + self.secret
        with patch.object(amap_source, "urlopen") as remote:
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(payload).encode() for payload in payloads
            ]
            map_sources.geocode("地址", "", self.root)
        for path in self.root.rglob("*"):
            if path.is_file():
                self.assertNotIn(self.secret, path.read_text(encoding="utf-8"))

    def test_city_only_success_leaves_no_map_center(self):
        payloads = [
            {
                "status": "1",
                "geocodes": [
                    {"formatted_address": "北京市", "level": "省", "location": "116.4,39.9"}
                ],
            },
            {"status": "1", "pois": []},
        ]
        with patch.object(amap_source, "urlopen") as remote:
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(payload).encode() for payload in payloads
            ]
            result = map_sources.geocode("找不到的楼栋", "", self.root)
        self.assertEqual(result.candidates, [])
        self.assertTrue(result.warnings)

    def test_neighborhood_center_never_impersonates_requested_building(self):
        payloads = [
            {
                "status": "1",
                "geocodes": [
                    {
                        "formatted_address": "北京市测试园",
                        "level": "住宅区",
                        "location": "116.4,39.9",
                    }
                ],
            },
            {
                "status": "1",
                "pois": [
                    {"id": "C", "name": "测试园", "address": "测试路8号", "location": "116.4,39.9"}
                ],
            },
        ]
        with patch.object(amap_source, "urlopen") as remote:
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(payload).encode() for payload in payloads * 2
            ]
            result = map_sources.geocode("测试园2号楼", "Building 2, Test Garden", self.root)
        self.assertEqual(result.candidates, [])
        self.assertTrue(any("具体楼栋" in text for text in result.warnings))

    def test_missing_poi_permission_retains_same_provider_address_with_warning(self):
        for code in ("10002", "10012", "10044"):
            payloads = [self.responses()[0], {"status": "0", "infocode": code}, self.responses()[0]]
            with (
                patch.object(amap_source, "urlopen") as remote,
                patch.object(map_sources, "_fetch") as photon,
            ):
                remote.return_value.__enter__.return_value.read.side_effect = [
                    json.dumps(p).encode() for p in payloads
                ]
                result = map_sources.geocode("测试园2号楼", "备用地址", self.root)
            self.assertEqual(len(result.candidates), 1)
            self.assertTrue(
                any("保留本次高德地址解析候选" in message for message in result.warnings)
            )
            self.assertEqual(remote.call_count, 3)
            photon.assert_not_called()
        with patch.object(amap_source, "urlopen") as remote, self.assertRaises(ValueError):
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(p).encode()
                for p in (self.responses()[0], {"status": "0", "infocode": "10003"})
            ]
            map_sources.geocode("测试园2号楼", "", self.root)

    def test_filtered_building_warning_does_not_deny_a_valid_match(self):
        payloads = self.responses()
        payloads[1]["pois"][0]["name"] = "测试园3号楼"
        payloads[1]["pois"][0]["address"] = "测试路8号"
        with patch.object(amap_source, "urlopen") as remote:
            remote.return_value.__enter__.return_value.read.side_effect = [
                json.dumps(payload).encode() for payload in payloads
            ]
            result = map_sources.geocode("测试园2号楼", "", self.root)
        self.assertEqual(len(result.candidates), 1)
        self.assertEqual(result.candidates[0].formatted_address, "北京市朝阳区测试园2号楼")
        self.assertTrue(any(text.startswith("已过滤") for text in result.warnings))
        self.assertFalse(any(text.startswith("高德候选未匹配") for text in result.warnings))


if __name__ == "__main__":
    unittest.main()
