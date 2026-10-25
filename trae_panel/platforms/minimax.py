# -*- coding: utf-8 -*-
"""MiniMax Code 适配器（每日签到 + 配额/积分）。

协议逆向自社区公开实现（Dondo 等），要点：

  每日签到：
    GET  {agent}/minimax-cloud/api/v1/signin/status   ← 返回 7 天签到面板（day.status: 2=可领, 3=已领）
    POST {agent}/minimax-cloud/api/v1/signin/claim    ← 领取（claim_result: 1=领取成功, 2=已领）
    官方规则：每日签到 400 分；第 4/7 天各 1000 分；一周累计 4000 分。

  身份：GET {agent}/v1/api/user/info 取 data.userInfo.realUserID（否则回退 JWT 里的 user.id）。
  配额：GET {platform}/v1/api/openplatform/coding_plan/remains（5 小时 / 周额度百分比）。
  积分：POST {agent}/matrix/api/v1/commerce/get_membership_info 取剩余积分。

  所有 {agent} 请求需带签名头：
    x-signature = md5(timestamp + SECRET + body)     （body: GET 为 ""，POST 为 "{}"）
    yy          = md5(encodeURIComponent(path?query) + "_" + "{}" + md5(unix) + "ooui")
    x-timestamp = timestamp 秒；token / Authorization 均为 Bearer accessToken。

凭据来源：本机 MiniMax Code 的 minimax-agent-config.json（含 tokens.accessToken）或直接粘贴 JWT。
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid as _uuid
from datetime import datetime
from urllib.parse import urlencode

import requests

from ..api import AuthExpired
from ..log import log

AGENT_URL = "https://agent.minimax.io"
PLATFORM_URL = "https://platform.minimax.io"
SIGNATURE_SECRET = "I*7Cf%WZ#S&%1RlZJ&C2"
VERSION_CODE = "22201"

USER_INFO_PATH = "/v1/api/user/info"
SIGN_IN_STATUS_PATH = "/minimax-cloud/api/v1/signin/status"
SIGN_IN_CLAIM_PATH = "/minimax-cloud/api/v1/signin/claim"
MEMBERSHIP_PATH = "/matrix/api/v1/commerce/get_membership_info"
REMAINS_PATH = "/v1/api/openplatform/coding_plan/remains"


# ---- 编码助手（对齐 JS 语义，签名必须逐字一致）----
def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def _form_encode(pairs) -> str:
    """对齐 JS URLSearchParams.toString()：安全字符 A-Za-z0-9 * - . _，空格转 +。"""
    out = []
    for k, v in pairs:
        out.append("{}={}".format(_form_component(str(k)), _form_component(str(v))))
    return "&".join(out)


def _form_component(s: str) -> str:
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789*-._"
    res = []
    for ch in s.encode("utf-8"):
        c = chr(ch)
        if c in safe:
            res.append(c)
        elif c == " ":
            res.append("+")
        else:
            res.append("%{:02X}".format(ch))
    return "".join(res)


def _encode_uri_component(s: str) -> str:
    """对齐 JS encodeURIComponent：安全字符 A-Za-z0-9 - _ . ! ~ * ' ( )。"""
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.!~*'()"
    res = []
    for ch in s.encode("utf-8"):
        c = chr(ch)
        res.append(c if c in safe else "%{:02X}".format(ch))
    return "".join(res)


def _b64url_decode(s: str) -> bytes:
    s = s + "=" * (-len(s) % 4)
    import base64
    return base64.urlsafe_b64decode(s)


def _jwt_user_id(token: str) -> str:
    try:
        payload = json.loads(_b64url_decode(token.split(".")[1]))
    except Exception:
        return ""
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    v = user.get("id")
    return v.strip() if isinstance(v, str) and v.strip() else ""


# ---- 账号归一化 ----
def _uuid_for(account: dict) -> str:
    if account.get("minimax_uuid"):
        return str(account["minimax_uuid"])
    seed = str(account.get("uid") or account.get("token") or "")
    h = _md5("minimax:" + seed)
    return "{}-{}-{}-{}-{}".format(h[:8], h[8:12], h[12:16], h[16:20], h[20:32])


def import_account(raw: str, label: str = "") -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("请粘贴 MiniMax Code 的 accessToken（或 minimax-agent-config.json）")
    token, uid, extra = "", "", {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            raise ValueError("凭据不是合法 JSON")
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            raise ValueError("凭据格式异常")
        tokens = parsed.get("tokens") if isinstance(parsed.get("tokens"), dict) else {}
        token = (tokens.get("accessToken") or parsed.get("accessToken")
                 or parsed.get("token") or "").strip()
        extra = parsed
    else:
        token = text
    if not token:
        raise ValueError("未识别到 accessToken，请检查粘贴内容")
    uid = _jwt_user_id(token) or str(extra.get("realUserId") or "") or token[-12:]
    account = {
        "platform": "minimax",
        "host": "agent.minimax.io",
        "uid": uid,
        "label": (label or "").strip() or ("MiniMax " + uid[-8:]),
        "token": token,
        "minimax_uuid": str(extra.get("uuid") or extra.get("minimax_uuid") or ""),
    }
    return account


# ---- 签名请求 ----
def _signed_request(account: dict, path: str, method: str, user_id: str,
                    timeout: int = 20):
    token = account.get("token") or ""
    unix = int(time.time() * 1000) // 1000 * 1000
    timestamp = unix // 1000
    body = "{}" if method == "POST" else ""
    offset = int(datetime.now().astimezone().utcoffset().total_seconds())
    pairs = [
        ("app_id", "3001"), ("biz_id", "3"), ("browser_name", "Chrome"),
        ("client", "desktop"), ("device_id", "12345678"), ("device_platform", "web"),
        ("is_desktop", "1"), ("lang", "en"), ("os_name", "macOS"), ("sys_language", "en"),
        ("timezone_offset", str(offset)), ("token", token), ("unix", str(unix)),
        ("user_id", user_id or "0"), ("uuid", _uuid_for(account)), ("version_code", VERSION_CODE),
    ]
    request_path = path + "?" + _form_encode(pairs)
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "token": token,
        "x-signature": _md5("{}{}{}".format(timestamp, SIGNATURE_SECRET, body)),
        "x-timestamp": str(timestamp),
        "yy": _md5("{}_{}{}ooui".format(_encode_uri_component(request_path),
                                        body or "{}", _md5(str(unix)))),
    }
    try:
        resp = requests.request(method, AGENT_URL + request_path, headers=headers,
                                data=body if method == "POST" else None, timeout=timeout)
    except requests.RequestException as e:
        raise RuntimeError("网络请求失败：{}".format(e))
    return resp


def _payload(resp, label: str) -> dict:
    if resp.status_code in (401, 403):
        raise AuthExpired("MiniMax 登录态已失效，请重新导出 config 并导入（{}）".format(label))
    try:
        data = resp.json() or {}
    except ValueError:
        raise RuntimeError("MiniMax {} 返回非 JSON（HTTP {}）".format(label, resp.status_code))
    br = data.get("base_resp") if isinstance(data.get("base_resp"), dict) else {}
    code = br.get("status_code")
    if code not in (0, None):
        raise RuntimeError("MiniMax {} 被拒绝：status_code={}".format(label, code))
    if resp.status_code >= 400:
        raise RuntimeError("MiniMax {} HTTP {}".format(label, resp.status_code))
    return data


def _resolve_real_user_id(account: dict) -> str:
    uid = account.get("uid") or "0"
    try:
        resp = _signed_request(account, USER_INFO_PATH, "GET", uid)
        data = _payload(resp, "身份")
    except AuthExpired:
        raise
    except Exception:
        return uid
    info = (data.get("data") or {}).get("userInfo") if isinstance(data.get("data"), dict) else {}
    real = (info or {}).get("realUserID")
    return real.strip() if isinstance(real, str) and real.strip() else uid


# ---- 签到 ----
def checkin(account: dict, cfg: dict):
    label = account.get("label") or account.get("uid")
    try:
        real_uid = _resolve_real_user_id(account)
        account["real_user_id"] = real_uid
        resp = _signed_request(account, SIGN_IN_STATUS_PATH, "GET", real_uid)
        data = _payload(resp, "签到状态")
    except AuthExpired as e:
        account.setdefault("status", {})["last_error"] = str(e)
        return False, str(e)
    except Exception as e:
        return False, "签到失败：{}".format(e)

    panel = (data.get("data") or {}).get("panel") if isinstance(data.get("data"), dict) else {}
    days = panel.get("days") if isinstance(panel, dict) else None
    days = days if isinstance(days, list) else []
    today = next((d for d in days if isinstance(d, dict) and d.get("is_today")), None)
    if today is None:
        return False, "签到失败：返回的签到面板里没有今天的数据"

    st = today.get("status")
    day_no = today.get("day_no")
    pts = today.get("points")
    if st == 3:
        detail = "今日已签到（第 {} 天，{} 积分）".format(day_no, pts)
        ok = True
    elif st != 2:
        return False, "当前不可领取（第 {} 天 status={}）".format(day_no, st)
    else:
        try:
            claim = _signed_request(account, SIGN_IN_CLAIM_PATH, "POST", real_uid)
            cdata = _payload(claim, "签到领取")
        except AuthExpired as e:
            return False, str(e)
        except Exception as e:
            return False, "领取失败：{}".format(e)
        d = cdata.get("data") if isinstance(cdata.get("data"), dict) else {}
        claim_result = d.get("claim_result")
        if claim_result == 1:
            ok, detail = True, "签到成功（第 {} 天，+{} 积分）".format(d.get("day_no") or day_no,
                                                                      d.get("points") or pts)
        elif claim_result == 2:
            ok, detail = True, "今日已签到"
        else:
            ok, detail = False, "签到未完成（claim_result={}）".format(claim_result)

    status = account.setdefault("status", {})
    status.update({"checked_in": bool(ok), "checked_at": time.time(),
                   "last_error": "" if ok else detail})
    try:
        _do_usage(account)
    except Exception:
        pass
    return ok, detail


# ---- 用量 / 配额 ----
def _do_usage(account: dict) -> dict:
    token = account.get("token") or ""
    packs = []
    remaining, total = None, None
    try:
        resp = requests.get(PLATFORM_URL + REMAINS_PATH,
                            headers={"Authorization": "Bearer " + token, "token": token}, timeout=20)
        data = resp.json() if resp.content else {}
        br = data.get("base_resp") if isinstance(data.get("base_resp"), dict) else {}
        code = br.get("status_code")
        if code == 0:
            mr = data.get("model_remains")
            cur = mr[0] if isinstance(mr, list) and mr else {}
            iv = cur.get("current_interval_remaining_percent")
            wk = cur.get("current_weekly_remaining_percent")
            if isinstance(iv, (int, float)):
                packs.append({"name": "5 小时额度", "desc": "5 小时额度剩余 {}%".format(int(iv)),
                              "kind": "quota", "remain": int(iv), "used": 100 - int(iv),
                              "size": 100, "created": "", "end": "", "source": "MiniMax"})
            if isinstance(wk, (int, float)):
                packs.append({"name": "周额度", "desc": "周额度剩余 {}%".format(int(wk)),
                              "kind": "quota", "remain": int(wk), "used": 100 - int(wk),
                              "size": 100, "created": "", "end": "", "source": "MiniMax"})
        elif code == 2062:
            packs.append({"name": "无 Token Plan", "desc": "无 Token Plan（不提供数值配额）",
                          "kind": "none", "remain": None, "used": None, "size": None,
                          "created": "", "end": "", "source": "MiniMax"})
    except Exception:
        pass
    # 剩余积分（membership）
    try:
        real_uid = account.get("real_user_id") or account.get("uid") or "0"
        resp = _signed_request(account, MEMBERSHIP_PATH, "POST", real_uid)
        data = _payload(resp, "积分")
        summary = (data.get("op_credit_summary") if isinstance(data.get("op_credit_summary"), dict) else {}) or {}
        bal = summary.get("total_remaining_amount")
        if isinstance(bal, (int, float)):
            remaining = round(float(bal), 2)
            packs.append({"name": "剩余积分", "desc": "剩余积分（Credit）",
                          "kind": "credit", "remain": remaining, "used": None,
                          "size": None, "created": "", "end": "", "source": "MiniMax"})
    except Exception:
        pass

    usage = {"total": total, "consumed": None, "remaining": remaining,
             "ratio": None, "packs": packs}
    status = account.setdefault("status", {})
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
    return _do_usage(account)
