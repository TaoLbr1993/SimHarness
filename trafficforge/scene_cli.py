"""TrafficForge command-line access; generation never starts a simulation run."""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成和查看 TrafficForge 场景（不启动仿真）")
    parser.add_argument(
        "--url",
        default=(
            os.getenv("TRAFFICFORGE_API_URL")
            or os.getenv("NL2MAP_API_URL")  # Compatibility with pre-rename deployments.
            or "http://127.0.0.1:8001"
        ),
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="列出已保存的场景")
    show = commands.add_parser("show", help="查看交通状态目标、车辆需求和参数")
    show.add_argument("task_id")
    publish = commands.add_parser("publish", help="把历史任务的输入保存到系统，不导入旧指标")
    publish.add_argument("task_id")
    generate = commands.add_parser("generate", help="根据语言生成预览草稿，不入库、不运行")
    generate.add_argument("description")
    generate.add_argument("--map-task-id")
    generate.add_argument("--mode", choices=("traffic", "autonomy"), default="traffic")
    generate.add_argument("--publish", action="store_true", help="显式直接添加到场景目录")
    args = parser.parse_args(argv)
    payload = None
    path = "/scenarios"
    method = "GET"
    if args.command == "generate":
        path, method = "/generate", "POST"
        payload = {
            "description": args.description,
            "output_kind": "scenario",
            "run_simulation": False,
            "map_task_id": args.map_task_id,
            "simulation_mode": args.mode,
            "publish_to_catalog": args.publish,
        }
    elif args.command in {"show", "publish"}:
        path += "/" + urllib.parse.quote(args.task_id.removeprefix("nl2-"), safe="")
        if args.command == "publish":
            path, method = path + "/publish", "POST"
    request = urllib.request.Request(
        args.url.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method,
    )
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            result = json.load(response)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if args.command != "generate" or result.get("status") == "generated" else 1
    except urllib.error.HTTPError as exc:
        print(
            f"请求失败 HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}",
            file=sys.stderr,
        )
    except (OSError, ValueError) as exc:
        print(f"无法访问场景服务：{exc}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
