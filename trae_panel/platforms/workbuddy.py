# -*- coding: utf-8 -*-
"""WorkBuddy / CodeBuddy（腾讯）适配器。

接口与出站头族逆向自官方客户端（并结合社区公开逆向成果交叉核对），要点：

  域：
    chat    https://copilot.tencent.com   对话 / growth 域（旅行、连登、任务）/ token 刷新
    billing https://www.codebuddy.cn      签到、余额、活跃上报
    web     https://www.workbuddy.cn      部分领奖接口
    global  https://www.workbuddy.ai      国际版账号（realm=global 时替换 chat/billing）

  信封：所有接口统一返回 {code, msg, data}；code != 0 视为业务错误。
  签到：POST {billing}/v2/billing/meter/daily-checkin（重复签到返回 code=10001/14001「已签到」）。
  余额：POST {billing}/v2/billing/meter/get-user-resource（企业号走 get-enterprise-user-usage）。
  刷新：POST {chat}/v2/plugin/auth/token/refresh（凭据走 X-Refresh-Token 头，无请求体）。

凭据来源：由本机 WorkBuddy 插件/客户端导出的 auth JSON（嵌套形或扁平形），
必须本机导出后粘贴到面板导入（服务端不接触客户端登录流程）。
"""

from __future__ import annotations

import hashlib
import json
import random
import secrets
import time
import uuid
from urllib.parse import quote

import requests

from ..api import AuthExpired
from ..log import log

# ---- 常量 ----
CHAT_CN = "https://copilot.tencent.com"
BILLING_CN = "https://www.codebuddy.cn"
WEB_CN = "https://www.workbuddy.cn"
GLOBAL = "https://www.workbuddy.ai"

ORIGIN_CN = "https://www.codebuddy.cn"
ORIGIN_GLOBAL = "https://www.workbuddy.ai"

CLIENT_VERSION = "5.5.4"      # 对齐官方桌面端分发包版本（UA / X-IDE-Version）
CLI_VERSION = "2.137.1"       # 对齐官方内置 CLI
HOST_TAG = "copilot.tencent.com"   # 账号去重用的稳定 host 标识

CHECKIN_PATH_V2 = "/v2/billing/meter/daily-checkin"
CHECKIN_PATH = "/billing/meter/daily-checkin"
RESOURCE_PATH_V2 = "/v2/billing/meter/get-user-resource"
RESOURCE_PATH = "/billing/meter/get-user-resource"
ENTERPRISE_USAGE_PATH = "/v2/billing/meter/get-enterprise-user-usage"
REPORT_PATH = "/v2/report"
REFRESH_PATH = "/v2/plugin/auth/token/refresh"

# 「今天已签到」关键词（上游对重复签到返回 code!=0）
ALREADY_MARKERS = ("已签到", "already")

# growth 域（{chat} + path，带 BillingHeaders）
BUDDY_INFO_PATH = "/activity/growth/buddy/info"
BUDDY_FIRST_PATH = "/activity/growth/buddy/first"
BUDDY_AGREEMENT_PATH = "/activity/growth/buddy/agreement"
TRAVEL_STATUS_PATH = "/activity/growth/buddy/travel/status"
TRAVEL_DEPART_PATH = "/activity/growth/buddy/travel/depart"
TRAVEL_CLAIM_PATH = "/activity/growth/buddy/travel/claim"
STREAK_PATH = "/activity/growth/streak"
REDEEM_PATH = "/activity/growth/redeem"
LOTTERY_SUMMARY_PATH = "/activity/growth/lottery/summary"
LOTTERY_DRAW_PATH = "/activity/growth/lottery/draw"
GIFT_PATH = "/billing/meter/claim-gift"           # {billing}
COMPENSATION_PATH = "/billing/meter/claim-compensation"

TRAVEL_LOCATION_ID = 4        # 4 个地点收益/时长区间相同，固定取 4（古镇客栈）
BUDDY_TASK_INCOMPLETE = "first_buddy task not completed yet"


class WBError(Exception):
    """WorkBuddy 上游业务/HTTP 错误。"""

    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status
        self.msg = msg


# ---- 账号归一化 ----
def _realm(account: dict) -> str:
    """账号域：显式 cn/global 优先，其次按 domain 后缀推断。"""
    r = (account.get("region") or "").strip().lower()
    if r in ("cn", "global"):
        return r
    d = (account.get("domain") or "").strip().lower()
    if d == "workbuddy.ai" or d.endswith(".workbuddy.ai"):
        return "global"
    return "cn"


def _chat_base(account: dict) -> str:
    return GLOBAL if _realm(account) == "global" else CHAT_CN


def _billing_base(account: dict) -> str:
    return GLOBAL if _realm(account) == "global" else BILLING_CN


def _origin(account: dict) -> str:
    return ORIGIN_GLOBAL if _realm(account) == "global" else ORIGIN_CN


def _lang(account: dict) -> str:
    return "en-US" if _realm(account) == "global" else "zh-CN"


def _stable_id(uid: str, purpose: str) -> str:
    """按 uid + 用途稳定派生 36 hex 设备/会话标识（跨重启恒定、账号间互异）。"""
    return hashlib.sha256(("wb2a:" + purpose + ":" + str(uid)).encode("utf-8")).hexdigest()[:36]


def _client_token() -> str:
    """前端 randomUUID 同款幂等令牌。"""
    return uuid.uuid4().hex


def _billing_headers(account: dict) -> dict:
    h = {
        "Authorization": "Bearer " + (account.get("token") or ""),
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-CodeBuddy-Request": "1",
        "Accept-Language": _lang(account),
        "User-Agent": "WorkBuddy/" + CLIENT_VERSION,
    }
    uid = account.get("uid") or ""
    if uid:
        h["X-User-Id"] = str(uid)
        h["X-Machine-ID"] = _stable_id(uid, "machine")
        h["X-Session-ID"] = _stable_id(uid, "session")
    ent = account.get("enterprise_id") or ""
    if ent:
        h["X-Enterprise-Id"] = str(ent)
        h["X-Tenant-Id"] = str(ent)
    if account.get("domain"):
        h["X-Domain"] = str(account["domain"])
    if account.get("device_token"):
        h["X-Device-Token"] = str(account["device_token"])
    return h


def _refresh_headers(account: dict) -> dict:
    """refresh 端点专属头：CommonHeaders + X-Refresh-Token（只允许出现在这里）。"""
    origin = _origin(account)
    platform = "WorkBuddy AI" if _realm(account) == "global" else "WorkBuddy"
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
        "User-Agent": "WorkBuddy/{} {}/{} CLI/{}".format(
            CLIENT_VERSION, platform, CLIENT_VERSION, CLI_VERSION),
        "X-CodeBuddy-Request": "1",
        "Accept-Language": _lang(account),
        "X-Refresh-Token": account.get("refresh_token") or "",
        "X-Auth-Refresh-Source": "plugin",
    }
    uid = account.get("uid") or ""
    if uid:
        h["X-Machine-ID"] = _stable_id(uid, "machine")
        h["X-Session-ID"] = _stable_id(uid, "session")
    if account.get("enterprise_id"):
        h["X-Enterprise-Id"] = str(account["enterprise_id"])
    return h


# ---- 请求 ----
def _call(method: str, url: str, headers: dict, body=None, timeout: int = 30):
    """发请求并解信封，返回 data；HTTP 非 2xx 或 code != 0 抛 WBError。"""
    try:
        r = requests.request(method, url, headers=headers,
                             json=body if body is not None else None, timeout=timeout)
    except requests.RequestException as e:
        raise WBError(0, "网络请求失败：{}".format(e))
    text = (r.text or "")[:300]
    if r.status_code >= 400:
        raise WBError(r.status_code, "HTTP {} {}".format(r.status_code, text))
    try:
        env = r.json()
    except ValueError:
        raise WBError(r.status_code, "返回不是合法 JSON：{}".format(text))
    code = env.get("code")
    if code not in (0, None):
        raise WBError(r.status_code, "code={} msg={}".format(code, env.get("msg") or ""))
    return env.get("data")


def _is_already(err) -> bool:
    msg = str(err)
    low = msg.lower()
    return any(m in msg or m.lower() in low for m in ALREADY_MARKERS)


# ---- 账号导入 ----
def import_account(raw: str, label: str = "") -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("请粘贴 WorkBuddy 凭据 JSON")
    try:
        parsed = json.loads(text)
    except ValueError:
        raise ValueError("凭据不是合法 JSON：请粘贴本机导出的 WorkBuddy auth JSON")
    if isinstance(parsed, list):
        parsed = parsed[0] if parsed else {}
    if not isinstance(parsed, dict):
        raise ValueError("凭据格式异常")

    if "auth" in parsed:                       # 嵌套形（插件 OAuth 输出）
        a = parsed.get("auth") or {}
        acc = parsed.get("account") or {}
        access = a.get("accessToken") or a.get("access_token") or ""
        refresh = a.get("refreshToken") or a.get("refresh_token") or ""
        expires = a.get("expiresAt") or 0
        if not expires and a.get("expires_in"):
            expires = int(time.time()) + int(a["expires_in"])
        domain = a.get("domain") or ""
        realm = a.get("realm") or ""
        uid = str(acc.get("uid") or "")
        ent = str(acc.get("enterpriseId") or acc.get("enterprise_id") or "")
        nick = acc.get("nickname") or ""
    else:                                      # 扁平形（含设备授权登录工具的 snake_case 输出）
        access = parsed.get("accessToken") or parsed.get("access_token") or ""
        refresh = parsed.get("refreshToken") or parsed.get("refresh_token") or ""
        expires = parsed.get("expiresAt") or 0
        if not expires and parsed.get("expires_in"):
            try:
                expires = int(time.time()) + int(parsed["expires_in"])
            except (TypeError, ValueError):
                expires = 0
        domain = parsed.get("domain") or ""
        realm = parsed.get("realm") or ""
        uid = str(parsed.get("uid") or "")
        ent = str(parsed.get("enterpriseId") or parsed.get("enterprise_id") or "")
        nick = parsed.get("nickname") or ""
    device_token = parsed.get("device_token") or ""

    if not access:
        raise ValueError("凭据中未含 accessToken，请在本机重新登录 WorkBuddy 后再导出")
    if not uid:
        raise ValueError("凭据中未含 uid，格式可能不完整")

    account = {
        "platform": "workbuddy",
        "host": HOST_TAG,
        "uid": uid,
        "label": (label or "").strip() or nick or ("workbuddy-" + uid[-6:]),
        "token": access,
        "refresh_token": refresh,
        "expired_at": int(expires) if expires else 0,
        "region": realm or ("global" if (domain or "").lower().endswith("workbuddy.ai") else "cn"),
        "domain": domain,
        "enterprise_id": ent,
        "device_token": device_token,
    }
    return account


# ---- 设备授权登录（面板内「一键登录」用）----
LOGIN_UA = "CLI/2.63.2 CodeBuddy/2.63.2"


def _login_base(realm: str):
    return (GLOBAL, ORIGIN_GLOBAL) if realm == "global" else (CHAT_CN, ORIGIN_CN)


def _login_headers(origin: str, token: str = "") -> dict:
    """设备授权登录流程的头族（与计费域不同：需带 Origin/Referer 与 CLI UA）。"""
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
        "User-Agent": LOGIN_UA,
    }
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def login_start(realm: str = "cn") -> dict:
    """发起设备授权登录，返回 {state, auth_url, realm}；前端据此打开新窗口授权。"""
    realm = "global" if (realm or "").lower() == "global" else "cn"
    base, origin = _login_base(realm)
    try:
        data = _call("POST", base + "/v2/plugin/auth/state?platform=CLI",
                     _login_headers(origin), {})
    except WBError as e:
        raise RuntimeError("获取授权链接失败：{}".format(e.msg))
    state = (data or {}).get("state") or ""
    url = (data or {}).get("authUrl") or ""
    if not state or not url:
        raise RuntimeError("上游未返回 state / authUrl，接口可能已变更")
    return {"state": state, "auth_url": url, "realm": realm}


def login_poll(state: str, realm: str = "cn") -> dict:
    """轮询一次登录结果；成功则取账号信息并落库。

    未完成返回 {"ok": False, "detail": ...}（HTTP 200，前端继续轮询即可）。
    """
    if not (state or "").strip():
        return {"ok": False, "detail": "缺少 state"}
    realm = "global" if (realm or "").lower() == "global" else "cn"
    base, origin = _login_base(realm)
    qs = "state=" + quote(str(state), safe="")
    try:
        tok = _call("GET", base + "/v2/plugin/auth/token?" + qs, _login_headers(origin))
    except WBError:
        return {"ok": False, "detail": "等待登录…"}
    access = (tok or {}).get("accessToken") or ""
    if not access:
        return {"ok": False, "detail": "等待登录…"}
    acct = {}
    try:
        acct = _call("GET", base + "/v2/plugin/login/account?" + qs,
                     _login_headers(origin, access)) or {}
    except WBError:
        pass
    raw = {
        "access_token": access,
        "refresh_token": (tok or {}).get("refreshToken") or "",
        "expires_in": (tok or {}).get("expiresIn") or 0,
        "domain": (tok or {}).get("domain") or "",
        "realm": realm,
        "uid": acct.get("uid") or "",
        "enterprise_id": acct.get("enterpriseId") or "",
        "nickname": acct.get("nickname") or "",
    }
    account = import_account(json.dumps(raw, ensure_ascii=False), "")
    from ..store import upsert_account
    saved = upsert_account(account)
    log("WorkBuddy 登录成功，账号已写入：{}（uid={}）".format(
        saved.get("label"), saved.get("uid")), "system")
    return {"ok": True, "account": {k: v for k, v in saved.items()
                                    if k not in ("token", "refresh_token", "device_token")}}


# ---- token 刷新 ----
def _refresh(account: dict) -> bool:
    if not (account.get("refresh_token") or "").strip():
        return False
    try:
        data = _call("POST", _chat_base(account) + REFRESH_PATH,
                     _refresh_headers(account), None, timeout=30)
    except WBError as e:
        log("WorkBuddy 刷新 token 失败（{}）：{}".format(account.get("label"), e.msg), "task")
        return False
    token = (data or {}).get("accessToken") or ""
    if not token:
        return False
    account["token"] = token
    if (data or {}).get("refreshToken"):
        account["refresh_token"] = data["refreshToken"]
    if (data or {}).get("domain"):
        account["domain"] = data["domain"]
    expires_in = int((data or {}).get("expiresIn") or 0)
    if 0 < expires_in < 10 * 365 * 86400:
        account["expired_at"] = int(time.time()) + expires_in
    return True


def _ensure_token(account: dict) -> None:
    """access 快过期（<2h）或已过期时先刷新；刷新失败且无法继续时抛 AuthExpired。"""
    exp = int(account.get("expired_at") or 0)
    if exp and exp - time.time() > 7200:
        return
    if _refresh(account):
        return
    if exp and exp - time.time() <= 0:
        raise AuthExpired("WorkBuddy 登录态已过期且刷新失败，请重新导出并导入凭据")
    # 无 refreshToken 或刷新失败：继续用现有 token 试一次（可能仍有效）


def _billing_call(account: dict, method: str, path: str, body=None, base: str | None = None):
    return _call(method, (base or _billing_base(account)) + path,
                 _billing_headers(account), body, timeout=30)


def _growth_call(account: dict, method: str, path: str, body=None):
    """growth 域走 chatBase（copilot.tencent.com），头族仍是 BillingHeaders。"""
    return _call(method, _chat_base(account) + path,
                 _billing_headers(account), body, timeout=30)


# ---- 签到 ----
def checkin(account: dict, cfg: dict):
    label = account.get("label") or account.get("uid")
    try:
        _ensure_token(account)
    except AuthExpired as e:
        account.setdefault("status", {})["last_error"] = str(e)
        return False, str(e)

    ok, detail = True, ""
    paths = ([CHECKIN_PATH, CHECKIN_PATH_V2] if _realm(account) == "global"
             else [CHECKIN_PATH_V2])
    last = None
    for i, p in enumerate(paths):
        try:
            _billing_call(account, "POST", p, {})
            detail = "签到成功"
            break
        except WBError as e:
            last = e
            if _is_already(e):
                detail = "今日已签到"
                break
            # 仅 404（路径不存在）换路径，其它错误直接判定
            if e.status != 404 or i == len(paths) - 1:
                if e.status == 401:
                    account.setdefault("status", {})["last_error"] = e.msg
                    return False, "登录态已失效，请重新导入凭据（{}）".format(e.msg)
                ok = False
                detail = "签到失败：{}".format(e.msg)
                break
    if ok and not detail:
        ok, detail = False, "签到失败：{}".format(last.msg if last else "未知错误")

    try:
        u = _do_usage(account)
        detail = "{}（剩余 {}）".format(detail, u.get("remaining"))
    except Exception:
        pass

    status = account.setdefault("status", {})
    status.update({"checked_in": bool(ok), "checked_at": time.time(),
                   "last_error": "" if ok else detail})
    return ok, detail


# ---- 用量 ----
def _do_usage(account: dict) -> dict:
    status = account.setdefault("status", {})
    if account.get("enterprise_id"):
        data = _billing_call(account, "POST", ENTERPRISE_USAGE_PATH, {})
        used = float((data or {}).get("credit") or 0)
        limit = (data or {}).get("limitNum")
        if limit is None:
            limit = (data or {}).get("limit_num")
        limit = int(limit or 0)
        if limit < 0:
            total, remaining = -1.0, None
        else:
            total = float(limit)
            remaining = float(max(0, limit - int(used + 0.5)))
        packs = []
    else:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        end = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() + 365 * 101 * 86400))
        body = {"PageNumber": 1, "PageSize": 100, "ProductCode": "p_tcaca",
                "Status": [0, 3], "PackageEndTimeRangeBegin": now, "PackageEndTimeRangeEnd": end}
        data = _billing_call(account, "POST", RESOURCE_PATH_V2, body) or {}
        accounts = (((data.get("Response") or {}).get("Data") or {}).get("Accounts")) or []
        packs, total, remaining = [], 0.0, 0.0
        for p in accounts:
            csize = p.get("CycleCapacitySize") or 0
            if csize:
                size = float(csize)
                remain = float(p.get("CycleCapacityRemain") or 0)
            else:
                size = float(p.get("CapacitySize") or 0)
                remain = float(p.get("CapacityRemain") or 0)
            total += size
            remaining += remain
            packs.append({"desc": p.get("PackageName") or p.get("PackageCode") or "?",
                          "end": (p.get("CycleEndTime") or "")[:10]})
    consumed = None if total < 0 else round(total - remaining, 2)
    usage = {
        "total": None if total < 0 else total,
        "consumed": consumed,
        "remaining": remaining,
        "ratio": (round(consumed / total, 4) if (total and total > 0) else None),
        "packs": packs,
    }
    status["usage"] = usage
    status["usage_at"] = time.time()
    _snapshot(account, usage)
    return usage


def _snapshot(account: dict, usage: dict) -> None:
    from ..store import append_snapshot
    append_snapshot({
        "ts": time.time(), "account_id": account.get("id"), "label": account.get("label"),
        "total": usage.get("total"), "consumed": usage.get("consumed"),
        "remaining": usage.get("remaining"), "ratio": usage.get("ratio"),
        "packs": len(usage.get("packs") or []),
    })


def usage(account: dict, cfg: dict) -> dict:
    _ensure_token(account)
    return _do_usage(account)


# ---- 其他任务 ----
def _report_activity(account: dict, conversation_id: str, request_id: str = "") -> None:
    """上报一条对话活跃（chat_request_send），点亮连登并解锁领养前置。"""
    now = int(time.time() * 1000)
    request_id = request_id or conversation_id
    event = {
        "eventCode": "chat_request_send", "timestamp": now, "reportDelay": 0, "mode": "craft",
        "conversationId": conversation_id, "requestId": request_id, "inputLength": 12,
        "requestModelId": "deepseek-v4-flash", "requestModelName": "DeepSeek V4 Flash",
        "isPlan": False, "isAutoExecuteTerminal": False, "isAutoModify": False,
        "codebaseEnable": False, "maxToken": 0, "maxSteps": 0, "temperature": 0, "maxRetries": 0,
        "mentionContexts": [], "knowledgeId": [], "knowledgeName": [], "codebaseId": "",
        "mentionContextCount": 0, "command": "", "expertId": "", "recommendId": "",
        "skillId": "", "skillCount": 0, "totalCount": 0, "fileUri": "", "presentAt": now,
        "traceId": "", "rootRequestId": request_id, "parentConversationId": conversation_id,
        "agentName": "default", "agentType": "conversation", "userId": account.get("uid") or "",
    }
    _billing_call(account, "POST", REPORT_PATH, [event])


def _trigger_gate(account: dict):
    """成长体系只对 CN 个人账号开放：global / 企业号直接跳过。"""
    if _realm(account) == "global":
        return "global 账号无 CN 成长体系"
    if account.get("enterprise_id"):
        return "企业账号无个人成长体系"
    return None


def _run_travel(account: dict, cfg: dict):
    gate = _trigger_gate(account)
    if gate:
        return False, "跳过：{}".format(gate)
    try:
        info = _growth_call(account, "GET", BUDDY_INFO_PATH) or {}
        buddy = info.get("buddy")
        if not buddy:
            # 无猫：先上报点亮 first_buddy 前置，再同意协议 + 领养
            try:
                _report_activity(account, "wb2a-adopt-" + str(int(time.time() * 1000)))
                time.sleep(1.2)
            except Exception:
                pass
            _growth_call(account, "POST", BUDDY_AGREEMENT_PATH, {"agree": True})
            try:
                _growth_call(account, "POST", BUDDY_FIRST_PATH, {})
                return True, "已领养第一只猫（+300 积分）"
            except WBError as e:
                if e.status == 400 and BUDDY_TASK_INCOMPLETE in e.msg.lower():
                    return True, "领养前置未达标，明日重试"
                return False, "领养失败：{}".format(e.msg)
        st = _growth_call(account, "GET", TRAVEL_STATUS_PATH) or {}
        state = st.get("state")
        if state == "arrived":
            rid = st.get("record_id") or 0
            if not rid:
                return True, "已到站但缺 record_id，跳过"
            data = _growth_call(account, "POST", TRAVEL_CLAIM_PATH, {"record_id": rid}) or {}
            return True, "已领旅行奖励 +{}".format(data.get("reward_credit") or 0)
        if state == "idle":
            if st.get("daily_limit_reached"):
                return True, "今日已派出过"
            _growth_call(account, "POST", TRAVEL_DEPART_PATH, {"location_id": TRAVEL_LOCATION_ID})
            return True, "已派出旅行（地点 {}）".format(TRAVEL_LOCATION_ID)
        return True, "旅行中（record={}），无需操作".format(st.get("record_id") or 0)
    except AuthExpired as e:
        return False, str(e)
    except WBError as e:
        return False, "旅行任务失败：{}".format(e.msg)


def _run_idle(account: dict, cfg: dict):
    """活跃点亮 + 连登兑换 + 抽奖 + 礼包/补偿（幂等）。"""
    gate = _trigger_gate(account)
    if gate:
        return False, "跳过：{}".format(gate)
    notes = []
    try:
        _report_activity(account, "wb2a-active-" + str(int(time.time() * 1000)))
        notes.append("活跃已上报")
    except WBError as e:
        notes.append("活跃上报失败（{}）".format(e.msg))
    # 礼包 / 补偿（有则领，无则业务错误，静默）
    for path, name in ((GIFT_PATH, "新手礼包"), (COMPENSATION_PATH, "活动补偿")):
        try:
            d = _billing_call(account, "POST", path, {}) or {}
            if d.get("credit"):
                notes.append("{} +{}".format(name, d["credit"]))
        except WBError:
            pass
    # 连登兑换
    try:
        full = _growth_call(account, "GET", STREAK_PATH) or {}
        rs = full.get("redemption_status") or {}
        statuses = {"7d": rs.get("tier_7d_status"), "14d": rs.get("tier_14d_status"),
                    "28d": rs.get("tier_28d_status")}
        days = ((full.get("streak") or {}).get("days")) or 0
        for tier in rs.get("tiers") or []:
            t = tier.get("tier")
            if statuses.get(t) in ("locked", "claimed", None):
                continue
            try:
                _growth_call(account, "POST", REDEEM_PATH, {"tier": t, "client_token": _client_token()})
                notes.append("兑换 {} 档".format(t))
            except WBError:
                pass
        # 抽奖
        try:
            summary = _growth_call(account, "GET", LOTTERY_SUMMARY_PATH) or {}
            chances = int(summary.get("chances") or 0)
            for _ in range(chances):
                try:
                    _growth_call(account, "POST", LOTTERY_DRAW_PATH, {"client_token": _client_token()})
                except WBError:
                    break
            if chances:
                notes.append("抽奖 {} 次".format(chances))
        except WBError:
            pass
        notes.append("连登 {} 天".format(days))
    except WBError as e:
        notes.append("连登查询失败（{}）".format(e.msg))
    return True, "；".join(notes) if notes else "无操作"


def run_task(task_id: str, account: dict, cfg: dict):
    """面板「任务中心」除签到/用量外的平台任务分发。"""
    try:
        _ensure_token(account)
    except AuthExpired as e:
        return False, str(e)
    if task_id == "travel":
        ok, detail = _run_travel(account, cfg)
    elif task_id == "idle":
        ok, detail = _run_idle(account, cfg)
    else:
        raise NotImplementedError(task_id)
    account.setdefault("status", {})[task_id] = detail
    return ok, detail
