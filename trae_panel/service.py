# -*- coding: utf-8 -*-
"""业务编排：账号导入、多账号签到、任务执行、用量快照与通知。

关于「自动完成任务」：Trae CN 官方目前可验证的自动化接口只有每日签到与用量查询，
没有公开可用的「成长任务」类接口（社区逆向成果中亦无），因此任务中心只内置这两个
可验证任务，并预留扩展点（在 TASKS 里加条目 + 一个处理函数即可）。
"""

from __future__ import annotations

import json
import time
from datetime import datetime

from .api import AuthExpired, TraeClient, checkin_claim, checkin_status, user_usage
from . import gateway
from .config import DEFAULT_CONFIG, load_config
from .crypto import (DEFAULT_HOST, account_label, account_region, decrypt_auth_value,
                     device_info_from_storage, new_fingerprint)
from .log import log
from .notify import send_notifications
from .store import (append_checkin, append_snapshot, load_accounts, prune_history,
                    save_accounts, upsert_account)

# 任务注册表（扩展点）：id / 名称 / 说明 / 是否支持自动执行
TASKS = [
    {"id": "checkin", "name": "每日签到",
     "desc": "查询今日签到状态并领取积分（服务端按天幂等，重复执行安全）", "auto": True},
    {"id": "usage", "name": "用量快照",
     "desc": "查询积分总量 / 已消耗 / 剩余与积分包，并写入历史用于趋势图", "auto": True},
]


# ---- 数值助手 ----
def _num(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _reward_desc(st: dict) -> str:
    parts = []
    base = st.get("credits")
    extra = st.get("extra_credits")
    if base is not None:
        parts.append("今日签到奖励积分：{}".format(base))
    if extra:
        parts.append("额外奖励：{}".format(extra))
    return "；".join(parts)


def _packs_simple(packs: list) -> list:
    out = []
    for p in packs or []:
        info = p.get("entitlement_base_info") or {}
        end = info.get("end_time")
        end_str = ""
        if end:
            try:
                end_str = datetime.fromtimestamp(int(end)).strftime("%Y-%m-%d")
            except (TypeError, ValueError):
                end_str = ""
        out.append({"desc": p.get("display_desc") or info.get("entitlement_id") or "?",
                    "end": end_str})
    return out


# ---- 账号导入 ----
def import_account(blob: str, label: str = "") -> dict:
    """按登录态导入/更新账号。支持三种粘贴内容（都不需要本机装 Python）：

      1. 整份 storage.json（推荐给没装 Python 的机器：记事本打开，Ctrl+A/Ctrl+C 粘进来）
      2. export_local.py / export_local.ps1 导出的 JSON（含真实设备指纹）
      3. 只有密文串（`iCubeAuthInfo://icube.cloudide` 的值；此时设备指纹是随机的，
         签到可用，但 OpenAI 网关会被上游按风控限流）

    对话网关需要客户端真实设备指纹，所以优先用 1 或 2。
    """
    cfg = load_config()
    text = (blob or "").strip()
    extra: dict = {}
    if text.startswith("{") or text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else {}
            if isinstance(parsed, dict):
                if parsed.get("blob"):
                    extra = parsed
                    text = str(parsed["blob"]).strip()
                elif parsed.get("iCubeAuthInfo://icube.cloudide"):
                    # 整份 storage.json：服务端自己提取密文 + 真实设备指纹
                    di = device_info_from_storage(parsed)
                    extra = {k: v for k, v in di.items() if v}
                    text = str(parsed["iCubeAuthInfo://icube.cloudide"]).strip()
        except Exception:
            extra = {}

    auth = decrypt_auth_value(text)
    host = (auth.get("host") or DEFAULT_HOST).rstrip("/")
    uid = str(auth.get("userId") or "")
    fp = new_fingerprint()
    account = {
        "label": (label or "").strip() or account_label(auth, uid or "account"),
        "host": host,
        "uid": uid,
        "token": auth.get("token") or "",
        "refresh_token": auth.get("refreshToken") or "",
        "region": account_region(auth) or cfg.get("region_override") or "",
        "expired_at": auth.get("expiredAt") or "",
        "device_id": extra.get("device_id") or fp["device_id"],
        "machine_id": extra.get("machine_id") or fp["machine_id"],
    }
    if extra.get("device_id") or extra.get("machine_id"):
        account["from_client"] = True
        for k in ("ide_version", "ide_version_code", "device_type", "os_version"):
            if extra.get(k):
                account[k] = extra[k]
    if not account["token"]:
        raise ValueError("登录态中未含 accessToken，请先在客户端重新登录后再导出")
    saved = upsert_account(account)
    log("账号已导入/更新：{}（uid={}，指纹来源={}）".format(
        saved.get("label"), saved.get("uid"), "客户端" if account.get("from_client") else "随机"), "system")
    return saved


def gateway_accounts() -> list:
    """给网关用的账号顺序：优先带真实指纹的（对话接口对指纹敏感）。"""
    accounts = load_accounts()
    with_fp = [a for a in accounts if a.get("from_client")]
    return with_fp + [a for a in accounts if not a.get("from_client")]


def gateway_models(cfg: dict) -> list:
    """汇总各账号可用模型（name -> display_name）。"""
    device_cfg = cfg.get("device") or {}
    out = {}
    for a in gateway_accounts():
        try:
            for m in gateway.fetch_models(a, device_cfg, cfg):
                out.setdefault(m["name"], m["display_name"])
        except Exception as e:
            log("获取模型列表失败（{}）：{}".format(a.get("label"), e), "system")
    return [{"name": k, "display_name": v} for k, v in out.items()]


def test_gateway_model(model: str) -> dict:
    """实测某个模型能否走通上游聊天接口（会消耗极少量积分）。"""
    cfg = load_config()
    gw = cfg.get("gateway") or {}
    device_cfg = cfg.get("device") or {}
    accounts = gateway_accounts()
    if not accounts:
        return {"ok": False, "detail": "还没有导入账号"}
    try:
        acc, first, rest = gateway.open_stream(
            accounts, device_cfg, model, [{"role": "user", "content": "ping"}],
            gw.get("intent") or "explain_code", cfg)
    except Exception as e:
        detail = str(e)
        if "unknown" in detail.lower():
            detail += "（该名字更像自定义/第三方模型，上游聊天接口不接受）"
        return {"ok": False, "detail": detail}
    try:
        gateway.collect_non_stream(model, first, rest)   # 读完并关闭连接
    except Exception:
        pass
    return {"ok": True, "detail": "可用", "account": acc.get("label")}


# ---- 用量快照 ----
def _snapshot_usage(account: dict, client: TraeClient, req_source: int) -> dict:
    data = user_usage(client, req_source)
    us = data.get("usage_summary") or {}
    packs = _packs_simple(data.get("user_entitlement_pack_list") or [])
    total = _num(us.get("total_amount"))
    consumed = _num(us.get("consumed_amount"))
    ratio = _num(us.get("consumption_ratio"))
    remaining = None if total is None else round(total - (consumed or 0), 2)
    status = account.setdefault("status", {})
    status["usage"] = {
        "total": total, "consumed": consumed, "remaining": remaining,
        "ratio": ratio, "packs": packs,
        "credit_billing": data.get("is_credits_billing", True),
    }
    status["usage_at"] = time.time()
    append_snapshot({
        "ts": time.time(), "account_id": account.get("id"), "label": account.get("label"),
        "total": total, "consumed": consumed, "remaining": remaining,
        "ratio": ratio, "packs": len(packs),
    })
    return status["usage"]


def refresh_usage(account_ids=None, do_notify=False) -> list:
    cfg = load_config()
    device_cfg = cfg.get("device") or {}
    req_source = cfg.get("req_source", 1)
    accounts = load_accounts()
    targets = [a for a in accounts if not account_ids or a.get("id") in account_ids]
    results = []
    for acc in targets:
        label = acc.get("label") or acc.get("uid")
        try:
            if cfg.get("region_override"):
                acc["region"] = cfg["region_override"]
            client = TraeClient(acc, device_cfg)
            _snapshot_usage(acc, client, req_source)
            results.append((label, True, "用量已刷新"))
            log("用量已刷新：{}".format(label), "usage")
        except AuthExpired as e:
            results.append((label, False, str(e)))
            log("用量刷新失败：{}：{}".format(label, e), "usage")
        except Exception as e:
            results.append((label, False, "用量查询失败：{}".format(e)))
            log("用量刷新失败：{}：{}".format(label, e), "usage")
    save_accounts(accounts)
    prune_history(cfg.get("history_keep_days", 90))
    if do_notify:
        send_notifications(cfg.get("notify"), results, title="Trae 用量刷新结果")
    return results


# ---- 签到 ----
def _checkin_one(acc: dict, cfg: dict, device_cfg: dict, req_source: int):
    label = acc.get("label") or acc.get("uid")
    if cfg.get("region_override"):
        acc["region"] = cfg["region_override"]
    client = TraeClient(acc, device_cfg)
    try:
        st = checkin_status(client, req_source)
    except AuthExpired as e:
        acc.setdefault("status", {})["last_error"] = str(e)
        return False, str(e)
    except Exception as e:
        detail = "查询签到状态失败：{}（登录态可能已过期）".format(e)
        acc.setdefault("status", {})["last_error"] = detail
        return False, detail

    ok, claimed = True, False
    if st.get("checked_in"):
        detail = "今日已签到（{}）".format(_reward_desc(st)) if _reward_desc(st) else "今日已签到"
    elif not st.get("enable"):
        ok = False
        detail = "签到当前不可用：{}".format(json.dumps(st, ensure_ascii=False)[:180])
    else:
        try:
            claim = checkin_claim(client, req_source)
            code = claim.get("code")
            if code == 0:
                claimed = True
                detail = "签到成功（{}）".format(_reward_desc(claim) or _reward_desc(st) or "已领取")
            elif code in (10001, 11001, 11002, 11003):
                detail = "今日已签到"
            else:
                ok = False
                detail = "签到失败：{}".format(
                    claim.get("message") or json.dumps(claim, ensure_ascii=False)[:180])
        except Exception as e:
            ok = False
            detail = "领取积分失败：{}".format(e)

    # 顺带刷新用量，保持面板数据新鲜
    try:
        _snapshot_usage(acc, client, req_source)
    except Exception:
        pass

    status = acc.setdefault("status", {})
    status.update({
        "checked_in": bool(ok),
        "claimed_now": claimed,
        "credits": st.get("credits"),
        "extra_credits": st.get("extra_credits"),
        "enable": st.get("enable"),
        "checked_at": time.time(),
        "last_error": "" if ok else detail,
    })
    return ok, detail


def run_checkin(account_ids=None, do_notify=True) -> list:
    """对指定的（或全部）账号执行每日签到，返回 [(label, ok, detail)]。"""
    cfg = load_config()
    device_cfg = cfg.get("device") or {}
    req_source = cfg.get("req_source", 1)
    accounts = load_accounts()
    if not accounts:
        log("没有账号，跳过签到", "task")
        return []
    targets = [a for a in accounts if not account_ids or a.get("id") in account_ids]
    log("开始签到，共 {} 个账号".format(len(targets)), "task")
    results = []
    for acc in targets:
        label = acc.get("label") or acc.get("uid")
        ok, detail = _checkin_one(acc, cfg, device_cfg, req_source)
        results.append((label, ok, detail))
        append_checkin({"ts": time.time(), "account_id": acc.get("id"), "label": label,
                        "ok": ok, "detail": detail,
                        "credits": (acc.get("status") or {}).get("credits")})
        log("[{}] {}".format(label, detail), "task")
    save_accounts(accounts)
    prune_history(cfg.get("history_keep_days", 90))
    if do_notify:
        send_notifications(cfg.get("notify"), results)
    return results


# ---- 任务中心 ----
def run_task(task_id: str, account_ids=None, do_notify=False) -> list:
    if task_id == "checkin":
        return run_checkin(account_ids=account_ids, do_notify=do_notify)
    if task_id == "usage":
        return refresh_usage(account_ids=account_ids, do_notify=do_notify)
    raise ValueError("未知任务：{}".format(task_id))


def task_status() -> list:
    """任务中心：每个账号 × 每个任务的完成状态。"""
    out = []
    for t in TASKS:
        row = {"id": t["id"], "name": t["name"], "desc": t["desc"], "auto": t["auto"],
               "accounts": []}
        for a in load_accounts():
            st = a.get("status") or {}
            if t["id"] == "checkin":
                done = bool(st.get("checked_in"))
                extra = "奖励 {} / 额外 {}".format(st.get("credits"), st.get("extra_credits")) \
                    if st.get("checked_in") else (st.get("last_error") or "未执行")
            else:
                usage = st.get("usage") or {}
                done = usage.get("total") is not None
                extra = "剩余 {} / 总量 {}".format(usage.get("remaining"), usage.get("total")) \
                    if done else "未执行"
            row["accounts"].append({"account_id": a.get("id"), "label": a.get("label"),
                                    "done": done, "info": extra})
        out.append(row)
    return out


# ---- 通知测试 ----
def test_notify() -> dict:
    results = [("测试账号", True, "这是一条来自 Trae 面板的通知测试")]
    notify_cfg = load_config().get("notify") or {}
    if not notify_cfg.get("enabled"):
        return {"ok": False, "msg": "通知总开关未开启"}
    send_notifications(notify_cfg, results, title="Trae 面板通知测试")
    return {"ok": True, "msg": "已按已开启的渠道发送测试通知"}


# ---- 概览 ----
def overview() -> dict:
    cfg = load_config()
    accounts = load_accounts()
    checked = sum(1 for a in accounts if (a.get("status") or {}).get("checked_in"))
    total_remaining = 0.0
    total_consumed = 0.0
    for a in accounts:
        usage = (a.get("status") or {}).get("usage") or {}
        total_remaining += _num(usage.get("remaining")) or 0.0
        total_consumed += _num(usage.get("consumed")) or 0.0
    return {
        "account_count": len(accounts),
        "checked_count": checked,
        "total_remaining": round(total_remaining, 2),
        "total_consumed": round(total_consumed, 2),
        "req_source": cfg.get("req_source", 1),
        "schedule": cfg.get("schedule") or DEFAULT_CONFIG["schedule"],
        "notify_enabled": bool((cfg.get("notify") or {}).get("enabled")),
    }
