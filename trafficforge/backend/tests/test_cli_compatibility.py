"""Check new and legacy service configuration through the actual CLI request boundary."""

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import scene_cli


class CliCompatibilityTest(unittest.TestCase):
    def test_url_configuration_preserves_legacy_and_prefers_trafficforge(self):
        cases = [
            ({}, [], "http://127.0.0.1:8001"),
            ({"NL2MAP_API_URL": "http://legacy.example"}, [], "http://legacy.example"),
            (
                {
                    "TRAFFICFORGE_API_URL": "http://forge.example",
                    "NL2MAP_API_URL": "http://legacy.example",
                },
                [],
                "http://forge.example",
            ),
            (
                {"TRAFFICFORGE_API_URL": "http://forge.example"},
                ["--url", "http://explicit.example"],
                "http://explicit.example",
            ),
        ]
        for environment, arguments, expected in cases:
            with (
                self.subTest(environment=environment, arguments=arguments),
                patch.dict(os.environ, environment, clear=True),
                patch.object(scene_cli.urllib.request, "urlopen") as remote,
                redirect_stdout(io.StringIO()),
            ):
                remote.return_value.__enter__.return_value = io.BytesIO(b"[]")
                self.assertEqual(scene_cli.main([*arguments, "list"]), 0)
                request = remote.call_args.args[0]
                self.assertEqual(request.full_url, expected + "/scenarios")
                self.assertEqual(request.get_method(), "GET")


if __name__ == "__main__":
    unittest.main()
