# -*- coding: utf-8 -*-
"""FastAPI 面板后端：多账号 CRUD、签到、任务、用量、配置、日志 + 定时调度。"""

from __future__ import annotations

import json
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import Body, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from . import gateway, platforms, service
from .config import BASE_DIR, load_config, save_config
from .log import log, recent
from .store import delete_account, load_accounts, load_history

PANEL_HTML = BASE_DIR / "panel" / "index.html"

_scheduler = BackgroundScheduler()


# ---- 鉴权 ----
def _require_key(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    expected = (load_config().get("admin_key") or "").strip()
    if not expected:
        return  # 未设置密钥：放行（建议仅内网/本机使用）
    if not x_admin_key or not secrets.compare_digest(x_admin_key, expected):
        raise HTTPException(status_code=401, detail="面板密钥错误或缺失")


def _public_account(a: dict) -> dict:
    """脱敏：对外不返回 token / refreshToken。"""
    out = {k: v for k, v in a.items() if k not in ("token", "refresh_token", "device_token")}
    return out


# ---- 定时调度 ----
def configure_scheduler() -> None:
    cfg = load_config()
    sch = cfg.get("schedule") or {}
    tz = sch.get("timezone") or "Asia/Shanghai"
    _scheduler.remove_all_jobs()

    if sch.get("checkin_enabled", True):
        for t in (sch.get("checkin_times") or []):
            try:
                hh, mm = str(t).split(":")
                _scheduler.add_job(service.run_checkin, CronTrigger(
                    hour=int(hh), minute=int(mm), timezone=tz),
                    id="checkin_{}".format(t), replace_existing=True)
            except Exception as e:
                log("排程解析失败（{}）：{}".format(t, e), "system")
    if sch.get("usage_enabled", True):
        minutes = int(sch.get("usage_interval_minutes") or 60)
        _scheduler.add_job(service.refresh_usage, IntervalTrigger(minutes=minutes),
                           id="usage_refresh", replace_existing=True)
    log("定时调度已更新：签到={} 用量间隔={}分钟".format(
        sch.get("checkin_times"), sch.get("usage_interval_minutes")), "system")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log("Trae 面板启动", "system")
    try:
        _scheduler.start()
        configure_scheduler()
    except Exception as e:
        log("调度器启动失败：{}".format(e), "system")
    yield
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        pass


app = FastAPI(title="Trae 多账号管理面板", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index():
    if PANEL_HTML.exists():
        return HTMLResponse(PANEL_HTML.read_text(encoding="utf-8"))
    return HTMLResponse("<h3>panel/index.html 缺失</h3>", status_code=500)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "service": "trae-panel"}


@app.post("/api/auth/verify")
def auth_verify(payload: dict = Body(default_factory=dict)):
    expected = (load_config().get("admin_key") or "").strip()
    key = (payload or {}).get("key") or ""
    ok = (not expected) or secrets.compare_digest(key, expected)
    return {"ok": ok, "required": bool(expected)}


# ---- 概览 / 账号 ----
@app.get("/api/overview", dependencies=[])
def api_overview(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return service.overview()


@app.get("/api/platforms")
def api_platforms(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return platforms.all_platforms()


@app.get("/api/accounts")
def api_accounts(platform: str = "",
                 x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return [_public_account(a) for a in load_accounts(platform or None)]


@app.post("/api/accounts")
def api_add_account(payload: dict = Body(...),
                    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    blob = (payload or {}).get("blob") or ""
    label = (payload or {}).get("label") or ""
    platform = (payload or {}).get("platform") or "trae"
    if not blob.strip():
        raise HTTPException(400, "请填写登录态内容")
    try:
        acc = service.import_account(blob, label, platform)
    except Exception as e:
        raise HTTPException(400, "导入失败：{}".format(e))
    return _public_account(acc)


@app.delete("/api/accounts/{account_id}")
def api_delete_account(account_id: str,
                       x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    if not delete_account(account_id):
        raise HTTPException(404, "账号不存在")
    return {"ok": True}


# ---- 平台内「一键登录」（设备授权，如 WorkBuddy）----
def _platform_login_fn(platform: str, name: str):
    ad = platforms.adapter(platform)
    fn = getattr(ad, name, None) if ad else None
    if fn is None:
        raise HTTPException(400, "「{}」不支持面板内一键登录".format(
            platforms.get(platform).get("name")))
    return fn


@app.post("/api/platforms/{platform}/login/start")
def api_platform_login_start(platform: str, payload: dict = Body(default_factory=dict),
                             x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    fn = _platform_login_fn(platform, "login_start")
    try:
        return fn((payload or {}).get("realm") or "cn")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(400, "发起登录失败：{}".format(e))


@app.post("/api/platforms/{platform}/login/poll")
def api_platform_login_poll(platform: str, payload: dict = Body(default_factory=dict),
                            x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    fn = _platform_login_fn(platform, "login_poll")
    return fn((payload or {}).get("state") or "", (payload or {}).get("realm") or "cn")


# ---- 签到 / 用量 ----
@app.post("/api/checkin")
def api_checkin(payload: dict = Body(default_factory=dict),
                x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    platform = (payload or {}).get("platform") or None
    ids = (payload or {}).get("account_ids") or None
    results = service.run_checkin(platform=platform, account_ids=ids, do_notify=False)
    return {"results": [{"label": l, "ok": o, "detail": d} for l, o, d in results]}


@app.post("/api/accounts/{account_id}/checkin")
def api_checkin_one(account_id: str,
                    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    results = service.run_checkin(account_ids=[account_id], do_notify=False)
    return {"results": [{"label": l, "ok": o, "detail": d} for l, o, d in results]}


@app.post("/api/usage/refresh")
def api_usage_refresh(payload: dict = Body(default_factory=dict),
                      x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    platform = (payload or {}).get("platform") or None
    ids = (payload or {}).get("account_ids") or None
    results = service.refresh_usage(platform=platform, account_ids=ids, do_notify=False)
    return {"results": [{"label": l, "ok": o, "detail": d} for l, o, d in results]}


@app.get("/api/usage/history")
def api_usage_history(days: int = Query(default=30, ge=1, le=365),
                      account_id: str = "",
                      x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    cutoff = time.time() - days * 86400
    snaps = [s for s in load_history().get("snapshots", [])
             if (s.get("ts") or 0) >= cutoff and (not account_id or s.get("account_id") == account_id)]
    # 每日每账号取最后一条，再汇总成趋势
    per_day = {}
    for s in snaps:
        day = datetime.fromtimestamp(s["ts"]).strftime("%Y-%m-%d")
        per_day[(day, s.get("account_id"))] = s
    agg = {}
    for (day, _aid), s in per_day.items():
        o = agg.setdefault(day, {"date": day, "remaining": 0.0, "consumed": 0.0, "total": 0.0})
        o["remaining"] += float(s.get("remaining") or 0)
        o["consumed"] += float(s.get("consumed") or 0)
        o["total"] += float(s.get("total") or 0)
    series = [agg[k] for k in sorted(agg.keys())]
    returns = [{"ts": s.get("ts"), "date": datetime.fromtimestamp(s["ts"]).strftime("%Y-%m-%d"),
                "account_id": s.get("account_id"), "label": s.get("label"),
                "total": s.get("total"), "consumed": s.get("consumed"),
                "remaining": s.get("remaining")} for s in sorted(snaps, key=lambda x: x.get("ts", 0))]
    return {"series": series, "snapshots": returns[-500:]}


@app.get("/api/checkins")
def api_checkins(days: int = Query(default=30, ge=1, le=365),
                 x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    cutoff = time.time() - days * 86400
    items = [c for c in load_history().get("checkins", []) if (c.get("ts") or 0) >= cutoff]
    for c in items:
        c["date"] = datetime.fromtimestamp(c["ts"]).strftime("%Y-%m-%d %H:%M")
    return items[-500:]


# ---- 任务 / 日志 / 配置 ----
@app.get("/api/tasks")
def api_tasks(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return {"platforms": service.task_status()}


@app.post("/api/tasks/run")
def api_tasks_run(payload: dict = Body(...),
                  x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    task_id = (payload or {}).get("task_id") or ""
    platform = (payload or {}).get("platform") or None
    ids = (payload or {}).get("account_ids") or None
    try:
        results = service.run_task(task_id, platform=platform, account_ids=ids, do_notify=False)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"results": [{"label": l, "ok": o, "detail": d} for l, o, d in results]}


@app.get("/api/logs")
def api_logs(limit: int = Query(default=200, ge=1, le=1000), channel: str = "",
             x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return recent(limit=limit, channel=channel)


@app.get("/api/config")
def api_get_config(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return load_config()


@app.post("/api/config")
def api_set_config(payload: dict = Body(...),
                   x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    cfg = save_config(payload or {})
    configure_scheduler()
    log("配置已更新", "system")
    return cfg


@app.post("/api/notify/test")
def api_notify_test(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    return service.test_notify()


# ---- OpenAI 兼容网关 ----
def _gateway_cfg():
    cfg = load_config()
    gw = cfg.get("gateway") or {}
    if not gw.get("enabled"):
        raise HTTPException(403, "网关未启用（面板「设置」里开启）")
    return cfg, gw


def _check_gateway_key(request: Request, gw: dict):
    expected = (gw.get("api_key") or "").strip()
    if not expected:
        return
    supplied = request.headers.get("authorization") or ""
    if supplied.lower().startswith("bearer "):
        supplied = supplied[7:].strip()
    else:
        supplied = request.headers.get("x-api-key") or ""
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "网关密钥错误：请在客户端填 Authorization: Bearer <key>")


@app.get("/api/gateway")
def api_gateway(x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    cfg = load_config()
    gw = cfg.get("gateway") or {}
    models = service.gateway_models(cfg) if gw.get("enabled") else []
    ups = [{"platform": a.get("platform"), "label": a.get("label"),
            "host": a.get("host"), "models": a.get("models") or []}
           for a in gateway.api_upstreams()]
    return {"enabled": bool(gw.get("enabled")), "api_key": gw.get("api_key") or "",
            "intent": gw.get("intent") or "", "models": models, "api_upstreams": ups,
            "account_count": len(service.gateway_accounts()),
            "keyed_accounts": sum(1 for a in service.gateway_accounts() if a.get("from_client"))}


@app.post("/api/gateway/test-model")
def api_gateway_test_model(payload: dict = Body(...),
                           x_admin_key: str | None = Header(default=None, alias="X-Admin-Key")):
    _require_key(x_admin_key)
    model = (payload or {}).get("model") or ""
    if not model:
        raise HTTPException(400, "缺少 model")
    return service.test_gateway_model(model)


@app.get("/v1/models")
def v1_models(request: Request):
    cfg, gw = _gateway_cfg()
    _check_gateway_key(request, gw)
    models = service.gateway_models(cfg)
    data = [{"id": m["name"], "object": "model", "created": int(time.time()),
             "owned_by": "trae", "display_name": m["display_name"]} for m in models]
    for m in gateway.api_models():          # API 型上游（DeepSeek/百炼/方舟…）
        data.append({"id": m["id"], "object": "model", "created": int(time.time()),
                     "owned_by": m["platform"], "display_name": m["label"] + " · " + m["model"]})
    return {"object": "list", "data": data}


@app.post("/v1/chat/completions")
async def v1_chat_completions(request: Request):
    cfg, gw = _gateway_cfg()
    _check_gateway_key(request, gw)
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "请求体不是合法 JSON")
    messages = payload.get("messages") or []
    if not messages:
        raise HTTPException(400, "messages 不能为空")
    stream = payload.get("stream")
    if stream is None:
        stream = True
    requested = payload.get("model") or ""

    # 优先匹配 API 型上游（DeepSeek / 小米 MiMo / 千帆 / 百炼 / 方舟 等）
    api_acc, api_model = gateway.resolve_api_upstream(requested)
    if api_acc:
        try:
            resp = gateway.openai_upstream_chat(api_acc, api_model, messages, bool(stream), cfg)
        except Exception as e:
            raise HTTPException(502, "上游调用失败：{}".format(e))
        if resp.status_code != 200:
            raise HTTPException(502, "上游 API 失败：HTTP {} {}".format(
                resp.status_code, (resp.text or "")[:200]))
        log("网关命中 API 上游「{}」→ 模型 {}（请求 {}）".format(
            api_acc.get("label"), api_model, requested), "gateway")
        if stream:
            return StreamingResponse(gateway.relay_stream(resp), media_type="text/event-stream",
                                     headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
        try:
            # 显式 UTF-8 解码：上游若声明 text/* 又不带 charset，requests 会按 latin-1 解出乱码
            return JSONResponse(json.loads(resp.content.decode("utf-8", "replace")))
        except ValueError:
            raise HTTPException(502, "上游返回非 JSON：{}".format(
                resp.content.decode("utf-8", "replace")[:200]))

    accounts = service.gateway_accounts()
    if not accounts:
        raise HTTPException(400, "还没有账号，请先在面板导入（建议用 export_local.py 的 JSON 以带上真实设备指纹）")
    device_cfg = cfg.get("device") or {}
    try:
        models = service.gateway_models(cfg)
    except Exception:
        models = []
    model = gateway.resolve_model(requested, models, cfg)
    intent = gw.get("intent") or "explain_code"
    try:
        acc, first, rest = gateway.open_stream(accounts, device_cfg, model, messages, intent, cfg)
    except Exception as e:
        msg = str(e)
        hint = ""
        if "unknown" in msg.lower():
            hint = ("｜模型「{}」不被上游聊天接口接受。面板清单里带 / 或 cn: 等前缀的多为自定义/第三方模型，"
                    "聊天接口用不了；请改用内置模型（seed_m8、deepseek-V3 等），"
                    "或用「API 接入」里的「测试」按钮逐个实测。").format(requested)
        raise HTTPException(502, "上游调用失败：{}{}".format(msg, hint))
    log("网关命中账号「{}」→ 模型 {}（请求 {}）".format(
        acc.get("label"), model, requested or "-"), "gateway")
    if stream:
        return StreamingResponse(gateway.openai_chunks(model, first, rest),
                                 media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
    try:
        return gateway.collect_non_stream(model, first, rest)
    except Exception as e:
        raise HTTPException(502, "上游调用失败：{}".format(e))
