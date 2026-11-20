"""Display classes refer only to roads present in the compiled scenario."""

import xml.etree.ElementTree as ET
from typing import Literal

DisplayLevel = Literal[
    "all", "main_roads", "motorway", "trunk", "primary", "secondary", "tertiary", "local"
]
MapOperation = Literal[
    "add_ew",
    "add_ns",
    "remove_ew",
    "remove_ns",
    "show_all",
    "show_main",
    "show_motorway",
    "show_trunk",
    "show_primary",
    "show_secondary",
    "show_tertiary",
    "show_local",
]
CLASS_LABELS = {
    "motorway": ("高速公路", "Motorways"),
    "trunk": ("干线公路", "Trunk roads"),
    "primary": ("一级道路", "Primary roads"),
    "secondary": ("二级道路", "Secondary roads"),
    "tertiary": ("三级道路", "Tertiary roads"),
    "local": ("支路", "Local roads"),
}


def visible_roads(edges: list[ET.Element], level: DisplayLevel | None) -> list[ET.Element]:
    if level in (None, "all"):
        return edges
    classes = {
        edge: {
            t.removeprefix("highway.").removesuffix("_link")
            for t in edge.get("type", "").split("|")
            if t.startswith("highway.")
        }
        for edge in edges
    }
    major = set(CLASS_LABELS) - {"local"}
    if level == "main_roads" and not any(classes.values()):
        highest = max((int(e.get("priority", "0")) for e in edges), default=0)
        return [e for e in edges if int(e.get("priority", "0")) == highest]
    if level == "main_roads":
        visible = [e for e in edges if classes[e] & major]
    elif level == "local":
        visible = [e for e in edges if classes[e] and not classes[e] & major]
    else:
        visible = [e for e in edges if level in classes[e]]
    if not visible:
        raise ValueError(
            "No roads of this class in the draft. 当前草稿没有该等级的道路，可显示全部道路。"
        )
    return visible
