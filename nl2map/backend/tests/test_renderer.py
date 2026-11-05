import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import renderer

NETWORK = """<net>
<edge id="in" from="outer" to="center">
  <lane id="in_0" index="0" shape="-100,-2 -8,-2" width="3.5"/>
</edge>
<edge id="out" from="center" to="outer">
  <lane id="out_0" index="0" shape="-8,2 -100,2" width="3.5"/>
</edge>
<junction id="center" type="{junction_type}" x="0" y="0" incLanes="in_0"
 shape="-8,-4 8,-4 8,4 -8,4"/>
{program}
{connections}
</net>"""
PROGRAM = (
    '<tlLogic id="signal" type="static" programID="0"><phase duration="30" state="G"/></tlLogic>'
)
CONNECTION = '<connection from="in" to="out" fromLane="0" toLane="0" tl="signal" linkIndex="0"/>'
SIGNAL_COLORS = {(239, 68, 68), (250, 204, 21), (34, 197, 94)}


class SignalRendererTest(unittest.TestCase):
    def render_colors(self, program="", connections="", junction_type="priority"):
        with tempfile.TemporaryDirectory() as tmp:
            network, image = Path(tmp, "network.net.xml"), Path(tmp, "review.png")
            network.write_text(
                NETWORK.format(
                    program=program, connections=connections, junction_type=junction_type
                ),
                encoding="utf-8",
            )
            renderer.render_network(str(network), str(image))
            with Image.open(image) as rendered:
                return {
                    color: count
                    for count, color in rendered.getcolors(rendered.width * rendered.height)
                }

    def test_unsignalized_network_has_no_signal_icons_even_with_legend(self):
        colors = self.render_colors()
        self.assertTrue(SIGNAL_COLORS.isdisjoint(colors))

    def test_bound_signal_is_visible_and_duplicate_turns_do_not_duplicate_heads(self):
        single = self.render_colors(PROGRAM, CONNECTION, "traffic_light")
        duplicate = self.render_colors(PROGRAM, CONNECTION + CONNECTION, "traffic_light")
        for color in SIGNAL_COLORS:
            self.assertGreater(single.get(color, 0), 100)
            self.assertEqual(single[color], duplicate[color])

    def test_junction_type_or_unbound_program_cannot_invent_a_signal_icon(self):
        for program, connections in (("", ""), (PROGRAM, ""), ("", CONNECTION)):
            with self.subTest(program=program, connections=connections):
                colors = self.render_colors(program, connections, "traffic_light")
                self.assertTrue(SIGNAL_COLORS.isdisjoint(colors))


if __name__ == "__main__":
    unittest.main()
