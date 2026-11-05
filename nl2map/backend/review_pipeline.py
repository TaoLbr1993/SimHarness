"""XML-authoritative acceptance with auxiliary visual feedback and bounded repairs."""

import json
from pathlib import Path

import agent
import config
import runner
from review_models import CheckResult, ReviewPlan, SignalRule, VisualIssue, VisualReview, XmlReview
from xml_checks import check_network


def _matching_signal_checks(
    plan: ReviewPlan, rules: XmlReview, issue: VisualIssue
) -> list[CheckResult]:
    """Resolve only the same predicate and spatial coverage; category alone is not evidence."""
    if issue.property != "signals" or issue.signal_claim is None:
        return []
    policy = {"coverage": "all_approaches", "presence": "present", "absence": "none"}[
        issue.signal_claim
    ]
    if issue.signal_claim == "coverage":
        if len({a.junction for a in rules.approaches}) != 1:
            return []
        actual = [a.direction for a in rules.approaches if a.incoming_lanes > 0]
        if "unknown" in actual or len(actual) != len(set(actual)):
            return []
        needed = set(issue.directions or actual)
    elif issue.directions:
        return []
    else:
        needed = set()
    for index, rule in enumerate(plan.rules, start=1):
        if not isinstance(rule, SignalRule) or rule.policy != policy:
            continue
        if rule.policy == "all_approaches":
            covered_directions = set(rule.directions or actual)
            if not needed.issubset(covered_directions):
                continue
        checks = [c for c in rules.checks if c.rule_id == f"R{index}"]
        if checks and all(c.status != "unverified" for c in checks):
            return checks
    return []


def combine_reviews(plan: ReviewPlan, rules: XmlReview, visual: VisualReview) -> dict:
    """Preserve disagreements; only executed requirement rules arbitrate their own properties."""
    confirmed = [c.model_dump() for c in rules.checks if c.status == "fail"]
    unverified = list(plan.unverified_requirements)
    unverified.extend(c.reason for c in rules.checks if c.status == "unverified")
    suspected, adjudications = [], []
    for issue in visual.issues:
        covered = _matching_signal_checks(plan, rules, issue)
        if issue.property in {"signals", "lanes", "connections", "topology"} and covered:
            adjudications.append(
                {
                    "visual_issue": issue.model_dump(),
                    "authority": "xml",
                    "rule_ids": sorted({c.rule_id for c in covered}),
                    "reason": "结构结论以对应 XML 规则为准；此图像疑点不作为单独修复指令。",
                    "xml_evidence": [c.model_dump() for c in covered],
                }
            )
        elif issue.property in {"geometry", "other"}:
            suspected.append(issue.model_dump())
        else:
            unverified.append(f"视觉结构疑点未被需求规则覆盖：{issue.problem}；{issue.reason}")
    outcome = (
        "repair"
        if confirmed
        else "review_required"
        if unverified
        else "repair"
        if suspected
        else "success"
    )
    details = [
        {
            "problem": c["problem"],
            "reason": c["reason"],
            "source": "xml",
            "certainty": "confirmed",
            "rule_id": c["rule_id"],
            "evidence": c["evidence"],
        }
        for c in confirmed
    ] + [
        {
            "problem": c["problem"],
            "reason": c["reason"],
            "source": "image",
            "certainty": "suspected",
            "location": c["location"],
        }
        for c in suspected
    ]
    return {
        "passed": outcome == "success",
        "evidence_mode": "rules_and_image",
        "outcome": outcome,
        "issues": [
            f"[{c['certainty']}/{c['source']}] {c['problem']}：{c['reason']}" for c in details
        ],
        "issue_details": details,
        "unverified_requirements": unverified,
        "adjudications": adjudications,
    }


def evaluate_candidate(code: str, plan: ReviewPlan) -> dict:
    """Execute, compile, render, check XML and inspect the image for one actual candidate."""
    execution = runner.run_generated_code(code)
    validation = runner.validate_network(execution)
    attempt = {
        "generated_code": code,
        "execution": execution,
        "validation": validation,
        "rule_review": None,
        "visual_review": None,
        "review": None,
    }
    if not validation["passed"]:
        return attempt
    directory = Path(config.WORKSPACES_DIR) / execution["task_id"]
    try:
        rules = check_network(str(directory / validation["network"]), plan)
        attempt["rule_review"] = {
            "passed": rules.passed,
            "source": "compiled_xml",
            **rules.model_dump(),
        }
        # Inspect the image even when requirement rules fail.
        visual = agent.review_geometry(
            plan.visual_requirements, str(directory / validation["image"])
        )
        attempt["visual_review"] = {
            "source": "image",
            "certainty": "suspected",
            **visual.model_dump(),
        }
        attempt["review"] = combine_reviews(plan, rules, visual)
    except Exception as exc:
        attempt["review_error"] = str(exc)
    return attempt


def repair_feedback(attempt: dict) -> dict:
    """Keep confirmed evidence separate from visual suspicions in the repair instruction."""
    validation = attempt["validation"]
    if not validation["passed"]:
        diagnostic = "\n".join(validation["issues"])
    else:
        review = attempt["review"]
        diagnostic = json.dumps(
            {
                "instruction": "优先修复 XML 确认的问题。图像问题为疑似；"
                "保留已通过的结构要求，不按冲突的视觉描述改写它们。",
                "issues": review["issue_details"],
                "xml_checks": attempt["rule_review"]["checks"],
                "unverified_requirements": review["unverified_requirements"],
            },
            ensure_ascii=False,
        )
    feedback = {"code": attempt["generated_code"], "diagnostic": diagnostic}
    if validation.get("image"):
        feedback["image_path"] = str(
            Path(config.WORKSPACES_DIR) / attempt["execution"]["task_id"] / validation["image"]
        )
    return feedback


def generate_checked(description: str) -> dict:
    """Run the default V1 policy; experimental legacy review modes remain in main."""
    base = {
        "description": description,
        "review_mode": "rules_and_image",
        "model_configured": config.MODEL_CONFIGURED,
    }
    try:
        plan = agent.plan_review(description)
    except Exception as exc:
        return {
            **base,
            "status": "agent_error",
            "error": f"验收清单提取失败：{exc}",
            "attempts": [],
        }
    base["review_plan"] = plan.model_dump()
    feedback, attempts = [], []
    for number in range(1, config.MAX_GENERATION_ATTEMPTS + 1):
        try:
            code = agent.generate_code(description, feedback)
        except Exception as exc:
            return {
                **base,
                "status": "agent_error",
                "error": f"生成调用失败：{exc}",
                "attempts": attempts,
            }
        attempt = {"attempt": number, **evaluate_candidate(code, plan)}
        attempts.append(attempt)
        response = {**base, **attempt, "attempts": attempts}
        if "review_error" in attempt:
            return {
                **response,
                "status": "validation_error",
                "error": "检查失败：" + attempt["review_error"],
            }
        review = attempt["review"]
        if review and review["outcome"] in {"success", "review_required"}:
            return {**response, "status": review["outcome"]}
        feedback.append(repair_feedback(attempt))
    if attempt["execution"]["timeout"]:
        status = "execution_timeout"
    elif attempt["execution"]["exit_code"] != 0:
        status = "execution_error"
    elif review and not any(item["source"] == "xml" for item in review["issue_details"]):
        status = "review_required"
    else:
        status = "validation_error"
    return {**response, "status": status}
