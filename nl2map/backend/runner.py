"""
代码执行模块：把大模型生成的代码存到一个独立的任务目录里执行，
并收集执行日志和生成的路网文件。
"""

import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime

import config
import renderer


def _child_environment() -> dict[str, str]:
    """Avoid passing common credential variables to generated programs or tools."""
    return {
        key: value
        for key, value in os.environ.items()
        if not re.search(r"(API_KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)", key, re.I)
        and key != "WSLENV"
    }


def _new_task_dir() -> str:
    """为每次请求创建一个独立目录，避免多次请求互相覆盖。"""
    task_id = datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    task_dir = os.path.join(config.WORKSPACES_DIR, task_id)
    os.makedirs(os.path.join(task_dir, "output"), exist_ok=True)
    return task_dir


def _collect_artifacts(task_dir: str) -> list:
    """扫描 output 目录，收集生成的所有文件（相对路径）。"""
    output_dir = os.path.join(task_dir, "output")
    artifacts = []
    for root, _, files in os.walk(output_dir):
        for f in files:
            full = os.path.join(root, f)
            rel = os.path.relpath(full, task_dir).replace(os.sep, "/")
            artifacts.append(rel)
    return sorted(artifacts)


def run_generated_code(code: str) -> dict:
    """
    执行大模型生成的代码。
    返回：task_id、退出码、标准输出、错误输出、耗时、生成的文件列表。
    """
    task_dir = _new_task_dir()
    script_path = os.path.join(task_dir, "generate_map.py")
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(code)

    t0 = time.time()
    try:
        result = subprocess.run(
            [sys.executable, "generate_map.py"],
            cwd=task_dir,
            capture_output=True,
            text=True,
            timeout=config.EXEC_TIMEOUT_SEC,
            env=_child_environment(),
        )
        elapsed = round(time.time() - t0, 2)
        return {
            "task_id": os.path.basename(task_dir),
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed_sec": elapsed,
            "artifacts": _collect_artifacts(task_dir),
            "timeout": False,
        }
    except subprocess.TimeoutExpired:
        elapsed = round(time.time() - t0, 2)
        return {
            "task_id": os.path.basename(task_dir),
            "exit_code": -1,
            "stdout": "",
            "stderr": f"代码执行超过 {config.EXEC_TIMEOUT_SEC} 秒，已被终止",
            "elapsed_sec": elapsed,
            "artifacts": _collect_artifacts(task_dir),
            "timeout": True,
        }


def validate_network(run_result: dict) -> dict:
    """Compile and inspect the files created by one roadgen execution."""
    if run_result["timeout"]:
        return {"passed": False, "issues": ["生成代码执行超时"], "facts": {}}
    if run_result["exit_code"] != 0:
        diagnostic = (run_result["stderr"] or run_result["stdout"] or "未知错误")[-8000:]
        return {"passed": False, "issues": ["生成代码执行失败：" + diagnostic], "facts": {}}

    artifacts = run_result["artifacts"]
    configs = [path for path in artifacts if path.endswith(".netccfg")]
    if (
        len(configs) != 1
        or not any(path.endswith(".nod.xml") for path in artifacts)
        or not any(path.endswith(".edg.xml") for path in artifacts)
    ):
        return {"passed": False, "issues": ["缺少唯一 netccfg、节点文件或道路文件"], "facts": {}}
    task_dir = os.path.join(config.WORKSPACES_DIR, run_result["task_id"])
    if not shutil.which("netconvert"):
        return {"passed": False, "issues": ["未找到 SUMO netconvert，无法验证路网"], "facts": {}}

    config_path = os.path.join(task_dir, configs[0])
    output_dir = os.path.dirname(config_path)
    network_path = os.path.join(output_dir, "validated.net.xml")
    image_path = os.path.join(output_dir, "review.png")
    image_rendered = False
    try:
        for path in artifacts:
            if path.endswith((".xml", ".netccfg")):
                ET.parse(os.path.join(task_dir, path))
        result = subprocess.run(
            ["netconvert", "-c", config_path, "--output-file", network_path],
            capture_output=True,
            text=True,
            timeout=config.EXEC_TIMEOUT_SEC,
            env=_child_environment(),
        )
        if result.returncode != 0:
            raise ValueError("netconvert 失败：" + (result.stderr or result.stdout)[-8000:])
        root = ET.parse(network_path).getroot()
        if root.tag != "net":
            raise ValueError("netconvert 未生成有效的 SUMO net 文件")
        edges = [edge for edge in root.findall("edge") if edge.get("function") != "internal"]
        junctions = [j for j in root.findall("junction") if j.get("type") != "internal"]
        if not edges or not junctions:
            raise ValueError("生成的 SUMO 网络没有道路或节点")
        renderer.render_network(network_path, image_path)
        image_rendered = True
        connections = [
            connection
            for connection in root.findall("connection")
            if not connection.get("from", "").startswith(":")
        ]
        traffic_lights = root.findall("tlLogic")
        facts = {
            "edges": [
                {
                    "id": e.get("id"),
                    "from": e.get("from"),
                    "to": e.get("to"),
                    "lanes": [
                        {
                            "id": lane.get("id"),
                            "length_m": lane.get("length"),
                            "width_m": lane.get("width"),
                            "speed_mps": lane.get("speed"),
                            "shape": lane.get("shape"),
                        }
                        for lane in e.findall("lane")
                    ],
                }
                for e in edges[:64]
            ],
            "junctions": [
                {"id": j.get("id"), "type": j.get("type"), "x": j.get("x"), "y": j.get("y")}
                for j in junctions[:64]
            ],
            "connections": [
                {
                    "from": c.get("from"),
                    "to": c.get("to"),
                    "from_lane": c.get("fromLane"),
                    "to_lane": c.get("toLane"),
                    "direction": c.get("dir"),
                    "traffic_light": c.get("tl"),
                }
                for c in connections[:128]
            ],
            "traffic_lights": [light.get("id") for light in traffic_lights[:64]],
            "truncated": (
                len(edges) > 64
                or len(junctions) > 64
                or len(connections) > 128
                or len(traffic_lights) > 64
            ),
        }
        if facts["truncated"]:
            raise ValueError("路网规模超过当前自动检查上限，无法完整判断需求")
        run_result["artifacts"] = _collect_artifacts(task_dir)
        return {
            "passed": True,
            "issues": [],
            "facts": facts,
            "warnings": result.stderr[-4000:],
            "network": os.path.relpath(network_path, task_dir).replace(os.sep, "/"),
            "image": os.path.relpath(image_path, task_dir).replace(os.sep, "/"),
        }
    except (ET.ParseError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result = {"passed": False, "issues": [str(exc)], "facts": {}}
        if image_rendered:
            result["image"] = os.path.relpath(image_path, task_dir).replace(os.sep, "/")
            run_result["artifacts"] = _collect_artifacts(task_dir)
        return result
