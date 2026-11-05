"""
FastAPI 后端入口。

Pipeline：
  前端自然语言描述
    → agent 生成调用 roadgen 的 Python 代码
    → runner 在独立任务目录执行代码
    → 收集 SUMO 路网文件
    → 返回给前端
"""

import os
import re
import shutil
from pathlib import Path
from typing import Literal

import agent
import config
import map_sources
import preview_refresh
import realmap
import review_pipeline
import runner
import scenario_catalog
import scenario_pipeline
import scenario_revision
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from scenario_models import ScenarioDraft, ScenarioResult, ScenarioRevision

app = FastAPI(
    title="TrafficForge",
    description="自然语言 → 路网与车流预览 → 用户确认添加 SUMO 场景",
    version="1.2.0",
    docs_url=None,  # 关掉默认文档页，下面用国内 CDN 自定义
)

# 允许前端跨域访问（学长的前端渲染部分会从别的端口/域名调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/docs", include_in_schema=False)
def custom_docs():
    """自定义文档页：Swagger UI 资源走国内镜像，避免默认 CDN 白屏。"""
    return get_swagger_ui_html(
        openapi_url="/openapi.json",
        title="TrafficForge - API Documentation",
        swagger_js_url="https://registry.npmmirror.com/swagger-ui-dist/5.20.0/files/swagger-ui-bundle.js",
        swagger_css_url="https://registry.npmmirror.com/swagger-ui-dist/5.20.0/files/swagger-ui.css",
    )


class GenerateRequest(BaseModel):
    description: str = Field(..., min_length=1, description="对路网的自然语言描述")
    review_mode: Literal["rules_and_image", "image_and_facts", "image_only"] = Field(
        "rules_and_image", description="默认 XML 规则优先、图像辅助；其余模式用于对照实验"
    )
    source_mode: Literal["auto", "synthetic", "real_map"] = Field(
        "auto", description="自动识别真实地点；synthetic 显式使用原 roadgen 流程"
    )
    selected_place_id: str | None = Field(
        None,
        pattern=r"^(?:[NWR][0-9]+|amap:[0-9a-f]{32})$",
        description="从实际返回的高德或 OSM 地点候选中选择 ID",
    )
    detail_level: Literal["auto", "main_roads", "detailed"] = Field(
        "auto", description="真实地图默认主干路；语言明确要求时细化，也可显式选择 detailed"
    )
    output_kind: Literal["auto", "map", "scenario"] = Field(
        "auto", description="自动识别路网或仿真测试请求；scenario 生成车流和可运行场景"
    )
    simulation_mode: Literal["traffic", "autonomy"] = Field(
        "traffic", description="交通仿真只配置车流数量；智驾仿真支持 L0–L5 等级组成"
    )
    ui_language: Literal["en", "zh"] = Field("en", description="Preview interface language")
    map_task_id: str | None = Field(None, pattern=r"^\d{8}_\d{6}_[0-9a-f]{6}$")
    run_simulation: bool = Field(False, description="默认只生成预览；true 显式执行一次试运行")
    publish_to_catalog: bool = Field(
        False, description="默认生成独立草稿，确认后调用 publish；true 兼容显式自动入库"
    )


@app.get("/")
def root():
    return {"msg": "TrafficForge is running. API documentation: /docs"}


@app.get("/health")
def health():
    """健康检查：顺便告诉调用方模型有没有配好。"""
    return {
        "status": "ok",
        "model_configured": config.MODEL_CONFIGURED,
        "geocoder_provider": map_sources.geocoder_provider(),
        "amap_configured": bool(config.AMAP_WEB_API_KEY),
    }


@app.get("/artifacts/{task_id}/{filename}")
def artifact(task_id: str, filename: str) -> FileResponse:
    """Download only files within a generated task's output directory."""
    if not re.fullmatch(r"\d{8}_\d{6}_[0-9a-f]{6}", task_id) or not re.fullmatch(
        r"[\w.-]+", filename
    ):
        raise HTTPException(404, "产物不存在")
    root = (Path(config.WORKSPACES_DIR) / task_id / "output").resolve()
    path = (root / filename).resolve()
    if path.parent != root:
        raise HTTPException(404, "产物不存在")
    try:
        localized = preview_refresh.refreshed_preview(root, filename)
    except (OSError, ValueError) as exc:
        raise HTTPException(503, "Preview refresh failed; please retry loading the image.") from exc
    if localized is not None:
        return FileResponse(localized, filename=filename, headers={"Cache-Control": "no-cache"})
    if not path.is_file():
        raise HTTPException(404, "产物不存在")
    return FileResponse(path, filename=filename)


@app.get("/scenarios", response_model=list[ScenarioDraft])
def saved_scenarios() -> list[ScenarioDraft]:
    """List saved scenario settings without starting an experiment."""
    return scenario_catalog.list_scenarios()


@app.get("/scenarios/{task_id}", response_model=ScenarioDraft)
def saved_scenario(task_id: str) -> ScenarioDraft:
    """View one scenario definition independently of run history."""
    try:
        return scenario_catalog.get_scenario(task_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "场景不存在或元数据无效") from exc


@app.post("/scenarios/{task_id}/publish", response_model=ScenarioDraft)
def publish_scenario_task(task_id: str) -> ScenarioDraft:
    """Confirm a generated draft or import a legacy task without running it."""
    try:
        return scenario_catalog.publish_existing(task_id)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(400, "任务不存在或没有完整可运行场景") from exc
    except OSError as exc:
        raise HTTPException(503, "暂时无法保存场景，请检查场景目录是否可写后重试") from exc


@app.get("/scenarios/{task_id}/files/{filename}")
def saved_scenario_file(task_id: str, filename: str) -> FileResponse:
    """Read a published input or preview file."""
    try:
        return FileResponse(scenario_catalog.scenario_file(task_id, filename))
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(404, "场景文件不存在") from exc


@app.post("/scenarios/{task_id}/revise", response_model=ScenarioResult)
def revise_scenario_task(task_id: str, request: ScenarioRevision) -> ScenarioResult:
    """Apply human scene settings to a new, unpublished draft without running a simulation."""
    try:
        return scenario_revision.revise_scenario(task_id, request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(503, "暂时无法更新场景草稿，请检查工作区是否可读写后重试") from exc


@app.post("/generate")
def generate(req: GenerateRequest):
    base = {
        "description": req.description,
        "source_mode": req.source_mode,
        "review_mode": req.review_mode,
        "model_configured": config.MODEL_CONFIGURED,
    }
    if not config.MODEL_CONFIGURED:
        return {**base, "status": "agent_error", "error": "模型未配置", "attempts": []}
    if not shutil.which("netconvert"):
        return {
            **base,
            "status": "validation_error",
            "error": "未找到 SUMO netconvert",
            "attempts": [],
        }

    if req.source_mode != "synthetic" or req.output_kind != "map":
        try:
            intent = agent.interpret_map_request(
                req.description,
                force_real=req.source_mode == "real_map" and req.map_task_id is None,
            )
        except Exception as exc:
            return {
                **base,
                "status": "agent_error",
                "error": f"意图解析失败：{exc}",
                "attempts": [],
            }
        if req.source_mode != "auto":
            intent.source_mode = req.source_mode
        if req.output_kind != "map" and (
            intent.scenario is not None or req.output_kind == "scenario"
        ):
            return scenario_pipeline.generate_scenario(
                req.description,
                intent,
                map_task_id=req.map_task_id,
                selected_place_id=req.selected_place_id,
                detail_level=None if req.detail_level == "auto" else req.detail_level,
                run_simulation=req.run_simulation,
                publish_to_catalog=req.publish_to_catalog,
                simulation_mode=req.simulation_mode,
                ui_language=req.ui_language,
            ).model_dump(mode="json")
        if intent.source_mode == "real_map":
            return realmap.generate_real_map(
                req.description,
                intent,
                req.selected_place_id,
                detail_level=None if req.detail_level == "auto" else req.detail_level,
                ui_language=req.ui_language,
            ).model_dump(mode="json")

    base["source_mode"] = "synthetic"
    if req.review_mode == "rules_and_image":
        return {**review_pipeline.generate_checked(req.description), "source_mode": "synthetic"}

    feedback = []
    attempts = []
    for number in range(1, config.MAX_GENERATION_ATTEMPTS + 1):
        try:
            code = agent.generate_code(req.description, feedback)
        except Exception as exc:
            return {
                **base,
                "status": "agent_error",
                "error": f"调用模型失败：{exc}",
                "attempts": attempts,
            }
        execution = runner.run_generated_code(code)
        validation = runner.validate_network(execution)
        review = None
        if validation["passed"]:
            try:
                image_path = os.path.join(
                    config.WORKSPACES_DIR, execution["task_id"], validation["image"]
                )
                facts = None if req.review_mode == "image_only" else validation["facts"]
                review = agent.review_network(req.description, facts, image_path)
            except Exception as exc:
                attempts.append(
                    {
                        "attempt": number,
                        "generated_code": code,
                        "execution": execution,
                        "validation": validation,
                        "review": None,
                        "review_error": str(exc),
                    }
                )
                return {
                    **base,
                    "status": "validation_error",
                    "error": f"需求检查失败：{exc}",
                    "generated_code": code,
                    "execution": execution,
                    "validation": validation,
                    "attempts": attempts,
                }
        attempt = {
            "attempt": number,
            "generated_code": code,
            "execution": execution,
            "validation": validation,
            "review": review,
        }
        attempts.append(attempt)
        if validation["passed"] and review and review["passed"]:
            return {
                **base,
                "status": "success",
                "generated_code": code,
                "execution": execution,
                "validation": validation,
                "review": review,
                "attempts": attempts,
            }
        issues = validation["issues"] if not validation["passed"] else review["issues"]
        retry = {"code": code, "diagnostic": "\n".join(issues)}
        if validation.get("image"):
            retry["image_path"] = os.path.join(
                config.WORKSPACES_DIR, execution["task_id"], validation["image"]
            )
        feedback.append(retry)

    status = (
        "execution_timeout"
        if execution["timeout"]
        else ("execution_error" if execution["exit_code"] != 0 else "validation_error")
    )
    return {
        **base,
        "status": status,
        "generated_code": code,
        "execution": execution,
        "validation": validation,
        "review": review,
        "attempts": attempts,
    }
