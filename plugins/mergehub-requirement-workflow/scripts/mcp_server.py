#!/usr/bin/env python3
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


DEFAULT_PLUGIN_NAME = "mergehub-requirement-workflow"
DEFAULT_PLUGIN_VERSION = "0.1.0"


def load_plugin_manifest():
    manifest_path = Path(__file__).resolve().parents[1] / ".codex-plugin" / "plugin.json"
    try:
        return json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


PLUGIN_MANIFEST = load_plugin_manifest()
PLUGIN_NAME = str(PLUGIN_MANIFEST.get("name") or DEFAULT_PLUGIN_NAME).strip() or DEFAULT_PLUGIN_NAME
PLUGIN_VERSION = str(PLUGIN_MANIFEST.get("version") or DEFAULT_PLUGIN_VERSION).strip() or DEFAULT_PLUGIN_VERSION
DATA_DIR = Path(
    os.environ.get("MERGEHUB_REQUIREMENT_WORKFLOW_DIR")
    or "~/.mergehub/requirement-workflow"
).expanduser()
DB_PATH = DATA_DIR / "worklog.sqlite3"
CONFIG_PATH = DATA_DIR / "config.json"
REQUIREMENT_NO_PATTERN = re.compile(r"\bREQ\d{6}\b")
REQUIREMENT_NO_EXACT_PATTERN = re.compile(r"^REQ\d{6}$")
FUNCTION_SLUG_PATTERN = re.compile(r"^[a-z0-9_]+$")
PLAN_STATUS_APPROVED = "approved"


def ensure_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS work_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                local_id TEXT NOT NULL UNIQUE,
                date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'codex',
                idempotency_key TEXT NOT NULL UNIQUE,
                project_id INTEGER NULL,
                project_name TEXT NULL,
                role TEXT NOT NULL DEFAULT 'development',
                role_label TEXT NOT NULL DEFAULT '研发',
                summary TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '[]',
                deliverables_json TEXT NOT NULL DEFAULT '[]',
                verification_json TEXT NOT NULL DEFAULT '[]',
                risks_json TEXT NOT NULL DEFAULT '[]',
                sp_ready_content TEXT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                synced INTEGER NOT NULL DEFAULT 0,
                remote_item_id INTEGER NULL,
                last_sync_error TEXT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def now_iso():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def today():
    return dt.date.today().isoformat()


def normalize_date(value):
    if not value:
        return today()
    return dt.date.fromisoformat(str(value)).isoformat()


def normalize_text(value, default=None, max_length=None):
    if value is None:
        return default
    normalized = str(value).strip()
    if not normalized:
        return default
    if max_length and len(normalized) > max_length:
        return normalized[:max_length]
    return normalized


def normalize_list(values, max_items=30, max_length=500):
    if values is None:
        return []
    if isinstance(values, str):
        values = values.splitlines()
    normalized = []
    for value in values:
        item = normalize_text(value, max_length=max_length)
        if item:
            normalized.append(item)
        if len(normalized) >= max_items:
            break
    return normalized


def normalize_role(value):
    text = normalize_text(value, "development", 40).lower()
    if text in {"dev", "development", "研发"}:
        return "development", "研发"
    if text in {"test", "testing", "测试"}:
        return "testing", "测试"
    if text in {"product", "pm", "产品"}:
        return "product", "产品"
    if text in {"ops", "operation", "operations", "运维"}:
        return "ops", "运维"
    return text, text


def is_requirement_no(value):
    return bool(REQUIREMENT_NO_EXACT_PATTERN.match(str(value or "")))


def extract_requirement_nos(text):
    return set(REQUIREMENT_NO_PATTERN.findall(str(text or "")))


def validate_function_slug(value):
    normalized = normalize_text(value, max_length=120)
    if not normalized or not FUNCTION_SLUG_PATTERN.match(normalized):
        raise ValueError("functionSlug must contain only lowercase letters, digits, and underscores.")
    return normalized


def build_development_branch(branch_type, function_slug, requirement_no):
    normalized_type = normalize_text(branch_type, max_length=20)
    normalized_type = normalized_type.lower() if normalized_type else ""
    if normalized_type not in {"feature", "hotfix"}:
        raise ValueError("branchType must be feature or hotfix.")
    if not is_requirement_no(requirement_no):
        raise ValueError("requirementNo must match REQ000001.")
    return f"{normalized_type}_{validate_function_slug(function_slug)}_{requirement_no}"


def validate_commit_message(message, requirement_no):
    if not is_requirement_no(requirement_no):
        raise ValueError("requirementNo must match REQ000001.")
    if requirement_no not in extract_requirement_nos(message):
        raise ValueError(f"Commit message must contain {requirement_no}.")
    return True


def json_dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def json_loads(value, fallback):
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def make_idempotency_key(payload):
    seed = json_dumps({
        "date": payload["date"],
        "projectName": payload.get("project_name"),
        "role": payload["role"],
        "summary": payload["summary"],
        "details": payload["details"],
        "deliverables": payload["deliverables"],
        "verification": payload["verification"],
        "risks": payload["risks"],
    })
    return "codex-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:32]


def row_to_item(row):
    return {
        "localId": row["local_id"],
        "date": row["date"],
        "source": row["source"],
        "idempotencyKey": row["idempotency_key"],
        "projectId": row["project_id"],
        "projectName": row["project_name"],
        "role": row["role"],
        "roleLabel": row["role_label"],
        "summary": row["summary"],
        "details": json_loads(row["details_json"], []),
        "deliverables": json_loads(row["deliverables_json"], []),
        "verification": json_loads(row["verification_json"], []),
        "risks": json_loads(row["risks_json"], []),
        "spReadyContent": row["sp_ready_content"],
        "metadata": json_loads(row["metadata_json"], {}),
        "synced": bool(row["synced"]),
        "remoteItemId": row["remote_item_id"],
        "lastSyncError": row["last_sync_error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def connect():
    ensure_storage()
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def read_config():
    config = {}
    if CONFIG_PATH.exists():
        try:
            config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            config = {}
    return {
        "apiBaseUrl": os.environ.get("MERGEHUB_API_BASE_URL") or config.get("apiBaseUrl"),
        "tokenName": os.environ.get("MERGEHUB_TOKEN_NAME") or config.get("tokenName") or "satoken",
        "tokenValue": os.environ.get("MERGEHUB_TOKEN_VALUE") or config.get("tokenValue"),
    }


def require_runtime_credentials(arguments):
    api_base_url = normalize_text(
        arguments.get("apiBaseUrl") or arguments.get("api_base_url") or arguments.get("baseUrl") or os.environ.get("MERGEHUB_API_BASE_URL"),
        max_length=500,
    )
    token_name = normalize_text(
        arguments.get("tokenName") or arguments.get("token_name") or os.environ.get("MERGEHUB_TOKEN_NAME"),
        "satoken",
        80,
    )
    token_value = normalize_text(
        arguments.get("tokenValue") or arguments.get("token_value") or os.environ.get("MERGEHUB_TOKEN_VALUE"),
        max_length=1000,
    )
    if not api_base_url:
        raise ValueError("apiBaseUrl is required. Ask the user for the current MergeHub address.")
    if not token_value:
        raise ValueError("tokenValue is required. Ask the user for their current MergeHub token.")
    return api_base_url.rstrip("/"), {token_name: token_value}


def tool_configure_mergehub(arguments):
    api_base_url = normalize_text(arguments.get("apiBaseUrl") or arguments.get("api_base_url"), max_length=500)
    token_name = normalize_text(arguments.get("tokenName") or arguments.get("token_name"), "satoken", 80)
    token_value = normalize_text(arguments.get("tokenValue") or arguments.get("token_value"), max_length=500)
    if not api_base_url:
        raise ValueError("apiBaseUrl is required.")
    if not token_value:
        raise ValueError("tokenValue is required.")
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json_dumps({"apiBaseUrl": api_base_url.rstrip("/"), "tokenName": token_name, "tokenValue": token_value}),
        encoding="utf-8",
    )
    try:
        os.chmod(CONFIG_PATH, 0o600)
    except OSError:
        pass
    return {"configured": True, "apiBaseUrl": api_base_url.rstrip("/"), "tokenName": token_name, "configPath": str(CONFIG_PATH)}


def tool_record_work_item(arguments):
    role, role_label = normalize_role(arguments.get("role"))
    role_label = normalize_text(arguments.get("roleLabel") or arguments.get("role_label"), role_label, 40)
    payload = {
        "date": normalize_date(arguments.get("date")),
        "source": normalize_text(arguments.get("source"), "codex", 40),
        "project_id": arguments.get("projectId") or arguments.get("project_id"),
        "project_name": normalize_text(arguments.get("projectName") or arguments.get("project_name"), max_length=120),
        "role": role,
        "role_label": role_label,
        "summary": normalize_text(arguments.get("summary"), max_length=300),
        "details": normalize_list(arguments.get("details")),
        "deliverables": normalize_list(arguments.get("deliverables")),
        "verification": normalize_list(arguments.get("verification")),
        "risks": normalize_list(arguments.get("risks")),
        "sp_ready_content": normalize_text(arguments.get("spReadyContent") or arguments.get("sp_ready_content"), max_length=12000),
        "metadata": arguments.get("metadata") if isinstance(arguments.get("metadata"), dict) else {},
    }
    if not payload["summary"]:
        raise ValueError("summary is required.")
    idempotency_key = normalize_text(
        arguments.get("idempotencyKey") or arguments.get("idempotency_key"),
        max_length=180,
    ) or make_idempotency_key(payload)
    local_id = "local-" + hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()[:32]
    timestamp = now_iso()
    with connect() as connection:
        existing = connection.execute(
            "SELECT * FROM work_items WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        values = (
            payload["date"],
            payload["source"],
            payload["project_id"],
            payload["project_name"],
            payload["role"],
            payload["role_label"],
            payload["summary"],
            json_dumps(payload["details"]),
            json_dumps(payload["deliverables"]),
            json_dumps(payload["verification"]),
            json_dumps(payload["risks"]),
            payload["sp_ready_content"],
            json_dumps(payload["metadata"]),
            timestamp,
        )
        if existing:
            connection.execute(
                """
                UPDATE work_items
                SET date = ?, source = ?, project_id = ?, project_name = ?, role = ?, role_label = ?,
                    summary = ?, details_json = ?, deliverables_json = ?, verification_json = ?,
                    risks_json = ?, sp_ready_content = ?, metadata_json = ?, updated_at = ?,
                    synced = 0, last_sync_error = NULL
                WHERE idempotency_key = ?
                """,
                (*values, idempotency_key),
            )
        else:
            connection.execute(
                """
                INSERT INTO work_items (
                    local_id, date, source, idempotency_key, project_id, project_name, role, role_label,
                    summary, details_json, deliverables_json, verification_json, risks_json,
                    sp_ready_content, metadata_json, synced, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    local_id,
                    payload["date"],
                    payload["source"],
                    idempotency_key,
                    payload["project_id"],
                    payload["project_name"],
                    payload["role"],
                    payload["role_label"],
                    payload["summary"],
                    json_dumps(payload["details"]),
                    json_dumps(payload["deliverables"]),
                    json_dumps(payload["verification"]),
                    json_dumps(payload["risks"]),
                    payload["sp_ready_content"],
                    json_dumps(payload["metadata"]),
                    timestamp,
                    timestamp,
                ),
            )
        connection.commit()
        row = connection.execute("SELECT * FROM work_items WHERE idempotency_key = ?", (idempotency_key,)).fetchone()
    return {"recorded": True, "item": row_to_item(row), "dbPath": str(DB_PATH)}


def markdown_for_items(items, date_value):
    lines = [f"# Codex 工作记录 - {date_value}", ""]
    for item in items:
        lines.append(f"## {item['summary']}")
        if item.get("projectName"):
            lines.append(f"- 项目：{item['projectName']}")
        lines.append(f"- 角色：{item.get('roleLabel') or item.get('role') or '研发'}")
        for title, key in [("工作内容", "details"), ("交付物", "deliverables"), ("验证方式", "verification"), ("风险与待确认", "risks")]:
            values = item.get(key) or []
            if values:
                lines.append(f"- {title}：")
                lines.extend([f"  - {value}" for value in values])
        if item.get("spReadyContent"):
            lines.append("")
            lines.append(item["spReadyContent"])
        lines.append("")
    return "\n".join(lines).strip()


def tool_get_today_worklog(arguments):
    date_value = normalize_date(arguments.get("date"))
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM work_items WHERE date = ? ORDER BY id ASC",
            (date_value,),
        ).fetchall()
    items = [row_to_item(row) for row in rows]
    return {"date": date_value, "items": items, "markdown": markdown_for_items(items, date_value)}


def post_json(url, headers, payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={**headers, "Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


def request_json(method, url, headers, payload=None):
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request_headers = dict(headers)
    if payload is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    with urllib.request.urlopen(request, timeout=20) as response:
        text = response.read().decode("utf-8")
        return json.loads(text) if text else None


def is_truthy(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y", "确认", "是"}
    return bool(value)


def get_argument_bool(arguments, *names):
    for name in names:
        if name in arguments:
            return is_truthy(arguments.get(name))
    return False


def latest_plan(task):
    if not isinstance(task, dict):
        return None
    plan = task.get("latestPlan") or task.get("latest_plan")
    return plan if isinstance(plan, dict) else None


def requirement_no_from_task(task):
    if not isinstance(task, dict):
        return None
    return normalize_text(task.get("requirementNo") or task.get("requirement_no"), max_length=20)


def plan_status(plan):
    if not isinstance(plan, dict):
        return None
    status = normalize_text(plan.get("planStatus") or plan.get("plan_status"), max_length=80)
    return status.lower() if status else None


def plan_identifier(plan):
    if not isinstance(plan, dict):
        return None
    plan_id = plan.get("id")
    plan_version = plan.get("planVersion") or plan.get("plan_version")
    if plan_id and plan_version:
        return f"id={plan_id}, version={plan_version}"
    if plan_id:
        return f"id={plan_id}"
    if plan_version:
        return f"version={plan_version}"
    return None


def build_unapproved_plan_message(task, plan):
    requirement_no = requirement_no_from_task(task) or "-"
    task_status = normalize_text(task.get("taskStatus") or task.get("task_status"), max_length=80) if isinstance(task, dict) else None
    current_plan_status = plan_status(plan) or "none"
    identifier = plan_identifier(plan)
    plan_text = f"{current_plan_status} ({identifier})" if identifier else current_plan_status
    return (
        f"MergeHub 方案未通过，不能静默开始开发。"
        f"需求 {requirement_no} 当前任务状态为 {task_status or '-'}，最新方案状态为 {plan_text}。"
        "请先询问用户：是否确认无视未通过的实现方案直接开发？"
        "只有得到用户明确确认后，才能再次调用 start_development_run 并传入 ignoreUnapprovedPlan=true。"
    )


def direct_development_context(task, branch_type, function_slug, requirement_no, plan):
    development_branch = build_development_branch(branch_type, function_slug, requirement_no)
    return {
        "mergeHubDevelopmentRunCreated": False,
        "directDevelopmentAllowed": True,
        "ignoredUnapprovedPlan": True,
        "requiresUserConfirmation": False,
        "requirementNo": requirement_no,
        "taskId": task.get("id") if isinstance(task, dict) else None,
        "taskStatus": task.get("taskStatus") if isinstance(task, dict) else None,
        "latestPlanStatus": plan_status(plan) or "none",
        "developmentBranch": development_branch,
        "message": (
            "用户已确认无视未通过的实现方案直接开发。"
            "MergeHub 未创建 development run；后续进度、commit、产物无法绑定 runId，建议方案通过后再补建开发运行并同步。"
        ),
    }


def tool_list_requirement_tasks(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    return request_json("GET", f"{api_base_url}/api/codex/tasks/my", headers)


def tool_get_requirement_task(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    task_id = arguments.get("taskId") or arguments.get("task_id")
    if task_id is None:
        raise ValueError("taskId is required.")
    return request_json("GET", f"{api_base_url}/api/codex/tasks/{task_id}", headers)


def tool_get_requirement_document(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    task_id = arguments.get("taskId") or arguments.get("task_id")
    requirement_identifier = normalize_text(
        arguments.get("requirementId")
        or arguments.get("requirement_id")
        or arguments.get("requirementNo")
        or arguments.get("requirement_no")
        or arguments.get("requirementUuid")
        or arguments.get("requirement_uuid"),
        max_length=120,
    )
    source_task = None
    if task_id is not None:
        source_task = request_json("GET", f"{api_base_url}/api/codex/tasks/{task_id}", headers)
        if not requirement_identifier and isinstance(source_task, dict):
            requirement_identifier = normalize_text(
                source_task.get("requirementId")
                or source_task.get("requirement_id")
                or source_task.get("requirementNo")
                or source_task.get("requirement_no"),
                max_length=120,
            )
    if not requirement_identifier:
        raise ValueError("taskId, requirementId, requirementNo, or requirementUuid is required.")
    encoded_identifier = urllib.parse.quote(str(requirement_identifier), safe="")
    requirement_document = request_json(
        "GET",
        f"{api_base_url}/api/codex/requirements/{encoded_identifier}/document",
        headers,
    )
    return {
        "sourceTask": source_task,
        "requirementDocument": requirement_document,
        "documentContentSource": "mergehub",
        "externalDocumentNotice": (
            "MergeHub currently returns the in-platform requirement description and documentUrl. "
            "If documentUrl points to an external DingTalk document, Codex can only read its body after that content is synced into MergeHub or otherwise made accessible."
        ),
    }


def tool_submit_requirement_plan(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    task_id = arguments.get("taskId") or arguments.get("task_id")
    plan_content = normalize_text(arguments.get("planContent") or arguments.get("plan_content"), max_length=20000)
    if task_id is None:
        raise ValueError("taskId is required.")
    if not plan_content:
        raise ValueError("planContent is required.")
    payload = {
        "planContent": plan_content,
        "riskSummary": normalize_text(arguments.get("riskSummary") or arguments.get("risk_summary"), max_length=4000),
    }
    return request_json("POST", f"{api_base_url}/api/codex/tasks/{task_id}/plans", headers, payload)


def tool_start_development_run(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    task_id = arguments.get("taskId") or arguments.get("task_id")
    requirement_no = normalize_text(arguments.get("requirementNo") or arguments.get("requirement_no"), max_length=20)
    branch_type = normalize_text(arguments.get("branchType") or arguments.get("branch_type"), max_length=20)
    function_slug = validate_function_slug(arguments.get("functionSlug") or arguments.get("function_slug"))
    if task_id is None:
        raise ValueError("taskId is required.")
    task = request_json("GET", f"{api_base_url}/api/codex/tasks/{task_id}", headers)
    requirement_no = requirement_no or requirement_no_from_task(task)
    if not requirement_no:
        raise ValueError("requirementNo is required when MergeHub task response does not include requirementNo.")
    build_development_branch(branch_type, function_slug, requirement_no)
    plan = latest_plan(task)
    if plan_status(plan) != PLAN_STATUS_APPROVED:
        if get_argument_bool(arguments, "ignoreUnapprovedPlan", "ignore_unapproved_plan", "approvalOverrideConfirmed", "approval_override_confirmed"):
            return direct_development_context(task, branch_type, function_slug, requirement_no, plan)
        raise ValueError(build_unapproved_plan_message(task, plan))
    payload = {
        "projectId": arguments.get("projectId") or arguments.get("project_id"),
        "repositoryId": arguments.get("repositoryId") or arguments.get("repository_id"),
        "baseBranch": normalize_text(arguments.get("baseBranch") or arguments.get("base_branch"), max_length=200),
        "branchType": branch_type,
        "functionSlug": function_slug,
        "codexSessionId": normalize_text(arguments.get("codexSessionId") or arguments.get("codex_session_id"), max_length=200),
    }
    return request_json("POST", f"{api_base_url}/api/codex/tasks/{task_id}/development-runs", headers, payload)


def tool_sync_development_progress(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    run_id = arguments.get("runId") or arguments.get("run_id")
    if run_id is None:
        raise ValueError("runId is required.")
    payload = {
        "runStatus": normalize_text(arguments.get("runStatus") or arguments.get("run_status"), max_length=80),
        "progressSummary": normalize_text(arguments.get("progressSummary") or arguments.get("progress_summary"), max_length=4000),
    }
    return request_json("PATCH", f"{api_base_url}/api/codex/development-runs/{run_id}", headers, payload)


def tool_record_development_commit(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    run_id = arguments.get("runId") or arguments.get("run_id")
    requirement_no = normalize_text(arguments.get("requirementNo") or arguments.get("requirement_no"), max_length=20)
    commit_message = normalize_text(arguments.get("commitMessage") or arguments.get("commit_message"), max_length=4000)
    if run_id is None:
        raise ValueError("runId is required.")
    if requirement_no:
        validate_commit_message(commit_message, requirement_no)
    payload = {
        "commitId": normalize_text(arguments.get("commitId") or arguments.get("commit_id"), max_length=200),
        "commitMessage": commit_message,
        "commitUrl": normalize_text(arguments.get("commitUrl") or arguments.get("commit_url"), max_length=1000),
        "authorName": normalize_text(arguments.get("authorName") or arguments.get("author_name"), max_length=120),
        "committedAt": normalize_text(arguments.get("committedAt") or arguments.get("committed_at"), max_length=80),
    }
    return request_json("POST", f"{api_base_url}/api/codex/development-runs/{run_id}/commits", headers, payload)


def tool_record_development_artifact(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    run_id = arguments.get("runId") or arguments.get("run_id")
    if run_id is None:
        raise ValueError("runId is required.")
    has_change = arguments.get("hasChange") if "hasChange" in arguments else arguments.get("has_change")
    raw_artifact_content = arguments.get("artifactContent")
    if raw_artifact_content is None:
        raw_artifact_content = arguments.get("artifact_content")
    artifact_content = None if raw_artifact_content is None else str(raw_artifact_content)
    if has_change is not False and (artifact_content is None or not artifact_content.strip()):
        raise ValueError(
            "artifactContent is required for changed artifacts and must contain the complete artifact content, not a summary."
        )
    payload = {
        "artifactType": normalize_text(arguments.get("artifactType") or arguments.get("artifact_type"), max_length=80),
        "artifactName": normalize_text(arguments.get("artifactName") or arguments.get("artifact_name"), max_length=200),
        "hasChange": has_change,
        "artifactContent": artifact_content,
        "externalUrl": normalize_text(arguments.get("externalUrl") or arguments.get("external_url"), max_length=1000),
        "projectId": arguments.get("projectId") or arguments.get("project_id"),
        "environment": normalize_text(arguments.get("environment"), max_length=80),
    }
    return request_json("POST", f"{api_base_url}/api/codex/development-runs/{run_id}/artifacts", headers, payload)


def tool_submit_development_self_test(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    task_id = arguments.get("taskId") or arguments.get("task_id")
    result = normalize_text(arguments.get("result"), max_length=80)
    if task_id is None:
        raise ValueError("taskId is required.")
    if not result:
        raise ValueError("result is required.")
    payload = {
        "developmentRunId": arguments.get("developmentRunId") or arguments.get("development_run_id"),
        "result": result,
        "environment": normalize_text(arguments.get("environment"), max_length=80),
        "scopeSummary": normalize_text(arguments.get("scopeSummary") or arguments.get("scope_summary"), max_length=4000),
        "testPoints": normalize_text(arguments.get("testPoints") or arguments.get("test_points"), max_length=8000),
        "evidenceUrl": normalize_text(arguments.get("evidenceUrl") or arguments.get("evidence_url"), max_length=1000),
        "commitSummary": normalize_text(arguments.get("commitSummary") or arguments.get("commit_summary"), max_length=4000),
        "artifactSummary": normalize_text(arguments.get("artifactSummary") or arguments.get("artifact_summary"), max_length=4000),
    }
    return request_json("POST", f"{api_base_url}/api/codex/tasks/{task_id}/self-tests", headers, payload)


def tool_mark_development_done(arguments):
    api_base_url, headers = require_runtime_credentials(arguments)
    task_id = arguments.get("taskId") or arguments.get("task_id")
    if task_id is None:
        raise ValueError("taskId is required.")
    return request_json("POST", f"{api_base_url}/api/codex/tasks/{task_id}/dev-done", headers)


def tool_sync_to_mergehub(arguments):
    config = read_config()
    api_base_url = normalize_text(arguments.get("apiBaseUrl") or arguments.get("api_base_url") or config.get("apiBaseUrl"), max_length=500)
    token_name = normalize_text(arguments.get("tokenName") or arguments.get("token_name") or config.get("tokenName"), "satoken", 80)
    token_value = normalize_text(arguments.get("tokenValue") or arguments.get("token_value") or config.get("tokenValue"), max_length=500)
    date_value = normalize_date(arguments.get("date")) if arguments.get("date") else None
    include_synced = bool(arguments.get("includeSynced") or arguments.get("include_synced"))
    if not api_base_url:
        raise ValueError("apiBaseUrl is required. Call configure_mergehub or pass apiBaseUrl.")
    if not token_value:
        raise ValueError("tokenValue is required. Call configure_mergehub or pass tokenValue.")
    query = "SELECT * FROM work_items"
    params = []
    clauses = []
    if date_value:
        clauses.append("date = ?")
        params.append(date_value)
    if not include_synced:
        clauses.append("synced = 0")
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY id ASC"

    synced = []
    failed = []
    headers = {token_name: token_value}
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
        for row in rows:
            item = row_to_item(row)
            payload = {
                "date": item["date"],
                "source": item["source"],
                "idempotencyKey": item["idempotencyKey"],
                "projectId": item["projectId"],
                "projectName": item["projectName"],
                "role": item["role"],
                "roleLabel": item["roleLabel"],
                "summary": item["summary"],
                "details": item["details"],
                "deliverables": item["deliverables"],
                "verification": item["verification"],
                "risks": item["risks"],
                "spReadyContent": item["spReadyContent"],
                "metadata": item["metadata"],
            }
            try:
                response = post_json(f"{api_base_url.rstrip('/')}/api/daily-report-items/ingest", headers, payload)
                remote_id = response.get("id") if isinstance(response, dict) else None
                connection.execute(
                    "UPDATE work_items SET synced = 1, remote_item_id = ?, last_sync_error = NULL, updated_at = ? WHERE local_id = ?",
                    (remote_id, now_iso(), item["localId"]),
                )
                synced.append({"localId": item["localId"], "remoteItemId": remote_id, "summary": item["summary"]})
            except Exception as exception:
                error = str(exception)
                connection.execute(
                    "UPDATE work_items SET last_sync_error = ?, updated_at = ? WHERE local_id = ?",
                    (error[:1000], now_iso(), item["localId"]),
                )
                failed.append({"localId": item["localId"], "summary": item["summary"], "error": error})
        connection.commit()
    return {"syncedCount": len(synced), "failedCount": len(failed), "synced": synced, "failed": failed, "dbPath": str(DB_PATH)}


def tool_health_check(arguments):
    ensure_storage()
    config = read_config()
    with connect() as connection:
        total = connection.execute("SELECT COUNT(*) FROM work_items").fetchone()[0]
        pending = connection.execute("SELECT COUNT(*) FROM work_items WHERE synced = 0").fetchone()[0]
    return {
        "ok": True,
        "dbPath": str(DB_PATH),
        "configPath": str(CONFIG_PATH),
        "configured": bool(config.get("apiBaseUrl") and config.get("tokenValue")),
        "apiBaseUrl": config.get("apiBaseUrl"),
        "tokenName": config.get("tokenName"),
        "totalItems": total,
        "pendingItems": pending,
    }


TOOLS = {
    "configure_mergehub": {
        "description": "Configure the MergeHub API base URL and Sa-Token header for daily-report sync.",
        "handler": tool_configure_mergehub,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string", "description": "MergeHub base URL, for example http://127.0.0.1:8080"},
                "tokenName": {"type": "string", "description": "Authentication header name, usually satoken"},
                "tokenValue": {"type": "string", "description": "Current MergeHub token value"},
            },
            "required": ["apiBaseUrl", "tokenValue"],
        },
    },
    "record_work_item": {
        "description": "Record a meaningful Codex work item into the local MergeHub workflow cache.",
        "handler": tool_record_work_item,
        "schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today."},
                "idempotencyKey": {"type": "string", "description": "Stable key for updating the same work item."},
                "projectId": {"type": "integer"},
                "projectName": {"type": "string"},
                "role": {"type": "string", "enum": ["development", "testing", "product", "ops"]},
                "summary": {"type": "string"},
                "details": {"type": "array", "items": {"type": "string"}},
                "deliverables": {"type": "array", "items": {"type": "string"}},
                "verification": {"type": "array", "items": {"type": "string"}},
                "risks": {"type": "array", "items": {"type": "string"}},
                "spReadyContent": {"type": "string"},
                "metadata": {"type": "object"},
            },
            "required": ["summary"],
        },
    },
    "get_today_worklog": {
        "description": "Return local Codex work records and Markdown for a date.",
        "handler": tool_get_today_worklog,
        "schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "YYYY-MM-DD. Defaults to today."}
            },
        },
    },
    "sync_to_mergehub": {
        "description": "Sync pending local Codex work records to MergeHub personal daily reports.",
        "handler": tool_sync_to_mergehub,
        "schema": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Only sync this YYYY-MM-DD date."},
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "includeSynced": {"type": "boolean", "description": "Resend already synced items."},
            },
        },
    },
    "list_requirement_tasks": {
        "description": "List requirement tasks assigned to the current MergeHub user through the Codex-facing API. Pass apiBaseUrl and tokenValue at runtime.",
        "handler": tool_list_requirement_tasks,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue"],
        },
    },
    "get_requirement_task": {
        "description": "Get one MergeHub requirement task by task id.",
        "handler": tool_get_requirement_task,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "taskId": {"type": "integer"},
            },
            "required": ["apiBaseUrl", "tokenValue", "taskId"],
        },
    },
    "get_requirement_document": {
        "description": "Get MergeHub requirement description, documentUrl, members, and linked tasks for planning. Pass taskId, requirementId, requirementNo, or requirementUuid at runtime.",
        "handler": tool_get_requirement_document,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "taskId": {"type": "integer", "description": "Requirement task id; preferred when working from an assigned Codex task."},
                "requirementId": {"type": "integer"},
                "requirementNo": {"type": "string", "description": "Requirement number such as REQ000003."},
                "requirementUuid": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue"],
        },
    },
    "submit_requirement_plan": {
        "description": "Upload a Codex implementation plan for a MergeHub requirement task.",
        "handler": tool_submit_requirement_plan,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "taskId": {"type": "integer"},
                "planContent": {"type": "string"},
                "riskSummary": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue", "taskId", "planContent"],
        },
    },
    "start_development_run": {
        "description": "Start a Codex development run only after the MergeHub task's latest implementation plan is approved. If the plan is not approved, ask the user before using ignoreUnapprovedPlan for direct local development without creating a MergeHub run.",
        "handler": tool_start_development_run,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "taskId": {"type": "integer"},
                "requirementNo": {"type": "string", "description": "Optional local validation target, for example REQ000001."},
                "projectId": {"type": "integer"},
                "repositoryId": {"type": "integer"},
                "baseBranch": {"type": "string"},
                "branchType": {"type": "string", "enum": ["feature", "hotfix"]},
                "functionSlug": {"type": "string"},
                "codexSessionId": {"type": "string"},
                "ignoreUnapprovedPlan": {
                    "type": "boolean",
                    "description": "Set to true only after the user explicitly confirms ignoring an unapproved MergeHub implementation plan. The tool then returns direct-development context and does not create a MergeHub development run.",
                },
                "approvalOverrideConfirmed": {
                    "type": "boolean",
                    "description": "Alias of ignoreUnapprovedPlan for explicit user-confirmed override.",
                },
            },
            "required": ["apiBaseUrl", "tokenValue", "taskId", "branchType", "functionSlug"],
        },
    },
    "sync_development_progress": {
        "description": "Sync Codex development-run progress back to MergeHub. Setting runStatus to completed only completes the run; submit a passed self-test and then mark development done to complete the task.",
        "handler": tool_sync_development_progress,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "runId": {"type": "integer"},
                "runStatus": {"type": "string"},
                "progressSummary": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue", "runId"],
        },
    },
    "record_development_commit": {
        "description": "Record one development commit for a MergeHub Codex run. If requirementNo is passed, the commit message is validated locally first.",
        "handler": tool_record_development_commit,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "runId": {"type": "integer"},
                "requirementNo": {"type": "string"},
                "commitId": {"type": "string"},
                "commitMessage": {"type": "string"},
                "commitUrl": {"type": "string"},
                "authorName": {"type": "string"},
                "committedAt": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue", "runId", "commitId", "commitMessage"],
        },
    },
    "record_development_artifact": {
        "description": "Record a complete development artifact for a MergeHub Codex run. For every changed artifact, artifactContent must contain the full deliverable text, never only a summary, excerpt, diff, ellipsis, or external URL. Human-readable guides, API docs, deployment instructions, test reports, and migration notes must use complete GitHub-Flavored Markdown with MARKDOWN or a *_DOC/*_GUIDE artifact type; SQL, JSON, YAML, XML, properties, and CSV must remain in their complete native format.",
        "handler": tool_record_development_artifact,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "runId": {"type": "integer"},
                "artifactType": {"type": "string", "description": "Artifact format or semantic type. Use MARKDOWN or a descriptive *_DOC/*_GUIDE type for human-readable Markdown documents; use SQL, JSON, YAML, XML, and other native types for machine-applied content."},
                "artifactName": {"type": "string", "description": "Artifact filename or descriptive name. Prefer an .md filename for Markdown documents and the real native extension for machine-applied files."},
                "hasChange": {"type": "boolean"},
                "artifactContent": {"type": "string", "description": "Complete artifact text with all statements, keys, ordering, and required formatting. Required when hasChange is not false; summaries, excerpts, diffs, omission markers, and external URLs are not substitutes."},
                "externalUrl": {"type": "string"},
                "projectId": {"type": "integer"},
                "environment": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue", "runId", "artifactType", "artifactName"],
        },
    },
    "submit_development_self_test": {
        "description": "Submit the developer self-test result for a MergeHub requirement task. Use result=passed before marking development done.",
        "handler": tool_submit_development_self_test,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "taskId": {"type": "integer"},
                "developmentRunId": {"type": "integer"},
                "result": {"type": "string", "description": "Self-test result, usually passed, failed, or blocked."},
                "environment": {"type": "string"},
                "scopeSummary": {"type": "string"},
                "testPoints": {"type": "string"},
                "evidenceUrl": {"type": "string"},
                "commitSummary": {"type": "string"},
                "artifactSummary": {"type": "string"},
            },
            "required": ["apiBaseUrl", "tokenValue", "taskId", "result"],
        },
    },
    "mark_development_done": {
        "description": "Mark a MergeHub development task as done after a passed developer self-test. MergeHub will move the requirement to testing when all development tasks are done.",
        "handler": tool_mark_development_done,
        "schema": {
            "type": "object",
            "properties": {
                "apiBaseUrl": {"type": "string"},
                "tokenName": {"type": "string"},
                "tokenValue": {"type": "string"},
                "taskId": {"type": "integer"},
            },
            "required": ["apiBaseUrl", "tokenValue", "taskId"],
        },
    },
    "health_check": {
        "description": "Check local MergeHub workflow storage and sync configuration.",
        "handler": tool_health_check,
        "schema": {"type": "object", "properties": {}},
    },
}


def rpc_result(message_id, result):
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def rpc_error(message_id, code, message):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": message}}


def content_response(value):
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, indent=2)}]}


def handle_request(message):
    method = message.get("method")
    message_id = message.get("id")
    params = message.get("params") or {}
    if method == "initialize":
        return rpc_result(message_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": PLUGIN_NAME, "version": PLUGIN_VERSION},
        })
    if method == "tools/list":
        return rpc_result(message_id, {
            "tools": [
                {
                    "name": name,
                    "description": spec["description"],
                    "inputSchema": spec["schema"],
                }
                for name, spec in TOOLS.items()
            ]
        })
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if name not in TOOLS:
            return rpc_error(message_id, -32602, f"Unknown tool: {name}")
        try:
            result = TOOLS[name]["handler"](arguments)
            return rpc_result(message_id, content_response(result))
        except Exception as exception:
            traceback.print_exc(file=sys.stderr)
            return rpc_error(message_id, -32000, str(exception))
    if message_id is None:
        return None
    return rpc_error(message_id, -32601, f"Unsupported method: {method}")


def main():
    ensure_storage()
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            response = handle_request(message)
        except Exception as exception:
            traceback.print_exc(file=sys.stderr)
            response = rpc_error(None, -32700, str(exception))
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
