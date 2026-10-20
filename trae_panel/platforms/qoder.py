# -*- coding: utf-8 -*-
"""Qoder（通义灵码）适配器（每日签到）。

协议逆向自社区公开实现（青龙脚本 Qoder-Daily / 官方活动规则），要点：

  域：
    cn     https://openapi.qoder.com.cn
    global https://openapi.qoder.sh

  签到（注意活动接口叫 campaigns，不是 checkin）：
    GET  {base}/sash/api/v1/me/campaigns?forceRefresh=true   ← 拉活动列表
    POST {base}/sash/api/v1/me/campaigns/{campaignId}/claim  ← 领取（actionType=CLAIM_BENEFIT）
    已领取：HTTP 409，或 result == "ALREADY_CLAIMED"，或 replayed == true。
    官方开窗时间：每天 10:00（UTC+8）。

  鉴权：`authorization: Bearer <access>`，access 形如 dt-xxx，refresh 形如 drt-xxx。
  续期：POST {base}/api/v1/deviceToken/refresh（drt-）或 /api/v1/jobToken/refresh（jrt-）。

  国际版额外需要设备风控三件套（Cosy-MachineToken/Code/Type，由本机 UMID 组件生成）；
  CN 不需要，缺省时如实报错而不谎报「已签」。

凭据来源：本机设备授权登录工具导出的 `access_token|refresh_token`（可多段），
或 JSON（含 region / uid）粘贴导入。
"""

from __future__ import annotations

import hashlib
import json
import os
import time

import requests

from ..api import AuthExpired
from ..log import log

REGIONS = {
    "cn": {"base": "https://openapi.qoder.com.cn", "label": "中国版"},
    "global": {"base": "https://openapi.qoder.sh", "label": "国际版"},
}
CAMPAIGNS_PATH = "/sash/api/v1/me/campaigns"
CAMPAIGNS_QUERY = "?forceRefresh=true"
DEVICE_REFRESH_PATH = "/api/v1/deviceToken/refresh"
JOB_REFRESH_PATH = "/api/v1/jobToken/refresh"

SASH_CLIENT_TYPE = "10"
DEFAULT_CLIENT_VERSION = "0.2.5"


def _region(account: dict) -> str:
    r = (account.get("region") or "").strip().lower()
    return r if r in REGIONS else "cn"


def _base(account: dict) -> str:
    return REGIONS[_region(account)]["base"]


def _machine_id(account: dict) -> str:
    if account.get("qoder_machine_id"):
        return str(account["qoder_machine_id"])
    return hashlib.sha256(("qoder:" + str(account.get("uid") or account.get("token") or ""))
                          .encode("utf-8")).hexdigest()[:32]


def _machine_hostname(account: dict) -> str:
    return "qoder-" + hashlib.sha256(("host:" + str(account.get("uid") or "")).encode("utf-8")).hexdigest()[:8]


def _machine_os() -> str:
    return "win32_x86_64" if os.name == "nt" else "linux_x86_64"


def _sash_headers(account: dict) -> dict:
    h = {
        "authorization": "Bearer " + (account.get("token") or ""),
        "cosy-clienttype": SASH_CLIENT_TYPE,
        "accept": "application/json",
        "accept-language": "zh-CN",
        "user-agent": "Qoder",
        "Cosy-Version": str(account.get("qoder_client_version") or DEFAULT_CLIENT_VERSION),
        "Cosy-MachineOS": _machine_os(),
        "Cosy-MachineId": _machine_id(account),
        "Cosy-MachineHostname": _machine_hostname(account),
    }
    # 国际版设备风控三件套（CN 侧为噪音，不注入）
    if _region(account) == "global":
        tok = account.get("machine_token") or ""
        code = account.get("machine_code") or ""
        mtype = account.get("machine_type") or ""
        if tok and code and mtype:
            h["Cosy-MachineToken"] = tok
            h["Cosy-MachineCode"] = code
            h["Cosy-MachineType"] = mtype
    return h


def _request(method: str, url: str, headers: dict, body=None, timeout: int = 20):
    try:
        return requests.request(method, url, headers=headers,
                                json=body if body is not None else None,
                                timeout=timeout, verify=False)
    except requests.RequestException as e:
        raise RuntimeError("网络请求失败：{}".format(e))


def _json(resp) -> dict:
    try:
        return resp.json() or {}
    except ValueError:
        return {}


def _first_text(*values) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, (int, float)):
            return str(v)
    return ""


# ---- 账号导入 ----
def import_account(raw: str, label: str = "") -> dict:
    text = (raw or "").strip()
    if not text:
        raise ValueError("请粘贴 Qoder 的 access_token（或 access_token|refresh_token）")
    region, uid, access, refresh = "cn", "", "", ""
    extra = {}
    if text.startswith("{"):
        try:
            parsed = json.loads(text)
        except ValueError:
            raise ValueError("凭据不是合法 JSON")
        if isinstance(parsed, list):
            parsed = parsed[0] if parsed else {}
        if not isinstance(parsed, dict):
            raise ValueError("凭据格式异常")
        access = _first_text(parsed.get("accessToken"), parsed.get("access"),
                             parsed.get("token"), parsed.get("access_token"))
        refresh = _first_text(parsed.get("refreshToken"), parsed.get("refresh"),
                              parsed.get("refresh_token"))
        uid = _first_text(parsed.get("uid"), parsed.get("userId"))
        region = _first_text(parsed.get("region"), parsed.get("realm")) or "cn"
        extra = parsed
    else:
        parts = [p.strip() for p in text.split("|")]
        if len(parts) == 1:
            access = parts[0]
        elif len(parts) == 2:
            access, refresh = parts
        elif len(parts) == 3:
            label, access, refresh = parts
        else:
            label, access, refresh, uid = parts[0], parts[1], parts[2], parts[3]
    region = region.strip().lower()
    if region in ("cn", "china", "中国", "zh"):
        region = "cn"
    elif region in ("global", "international", "国际", "intl"):
        region = "global"
    else:
        region = "cn"

    if not access:
        raise ValueError("未识别到 access_token（形如 dt-xxx），请检查粘贴内容")
    if region == "global":
        if not (extra.get("machine_token") and extra.get("machine_code") and extra.get("machine_type")):
            # 不阻断导入，但签到时会如实报错
            log("Qoder 国际版账号未提供设备风控三件套（machine_token/code/type），签到可能取不到活动", "system")

    account = {
        "platform": "qoder",
        "host": REGIONS[region]["base"].replace("https://", ""),
        "uid": uid or (str(access)[-12:] if not uid else uid),
        "label": (label or "").strip() or ("Qoder " + (uid or str(access)[:8])),
        "token": access,
        "refresh_token": refresh,
        "region": region,
        "qoder_machine_id": _first_text(extra.get("machine_id")),
    }
    for k_src, k_dst in (("machine_token", "machine_token"), ("machine_code", "machine_code"),
                         ("machine_type", "machine_type"), ("client_version", "qoder_client_version")):
        if extra.get(k_src):
            account[k_dst] = str(extra[k_src])
    return account


# ---- token 续期 ----
def _refresh(account: dict) -> bool:
    refresh = (account.get("refresh_token") or "").strip()
    if not refresh:
        return False
    path = JOB_REFRESH_PATH if refresh.startswith("jrt-") else DEVICE_REFRESH_PATH
    try:
        resp = _request("POST", _base(account) + path,
                        {"User-Agent": "qoder-local-proxy"}, {"refresh_token": refresh}, timeout=20)
    except RuntimeError as e:
        log("Qoder 续期失败（{}）：{}".format(account.get("label"), e), "task")
        return False
    if resp.status_code in (401, 403):
        return False
    data = _json(resp)
    inner = data.get("data") if isinstance(data.get("data"), dict) else data
    token = _first_text(inner.get("device_token"), inner.get("deviceToken"),
                        inner.get("token"), inner.get("access_token"))
    if not token:
        return False
    account["token"] = token
    account["refresh_token"] = _first_text(inner.get("refresh_token"),
                                           inner.get("refreshToken")) or refresh
    return True


def _fetch_campaigns(account: dict):
    resp = _request("GET", _base(account) + CAMPAIGNS_PATH + CAMPAIGNS_QUERY, _sash_headers(account))
    if resp.status_code in (401, 403) and account.get("refresh_token"):
        if _refresh(account):
            resp = _request("GET", _base(account) + CAMPAIGNS_PATH + CAMPAIGNS_QUERY,
                            _sash_headers(account))
    return resp


# ---- 签到 ----
def checkin(account: dict, cfg: dict):
    label = account.get("label") or account.get("uid")
    try:
        resp = _fetch_campaigns(account)
    except RuntimeError as e:
        return False, str(e)
    if resp.status_code in (401, 403):
        msg = "签到失败（HTTP {}）：凭证无效或地区选错，请重新导出".format(resp.status_code)
        account.setdefault("status", {})["last_error"] = msg
        return False, msg
    if resp.status_code == 429:
        return False, "签到失败（HTTP 429），请稍后重试"
    if resp.status_code != 200:
        return False, "活动列表返回 HTTP {}".format(resp.status_code)

    payload = _json(resp)
    rows = payload.get("campaigns")
    if not isinstance(rows, list):
        d = payload.get("data")
        rows = (d.get("campaigns") if isinstance(d, dict) else None)
    rows = rows if isinstance(rows, list) else []

    target, claimed = None, False
    for row in rows:
        if not isinstance(row, dict) or row.get("actionType") != "CLAIM_BENEFIT":
            continue
        st = row.get("claimStatus")
        if st == "CLAIMABLE" and target is None:
            target = row
        elif st == "CLAIMED":
            claimed = True

    ok, detail = True, ""
    if target is None:
        if claimed:
            detail = "今日已领取"
        elif _region(account) == "global":
            detail = ("未领取：国际版签到需本机 Qoder 客户端/CLI 的设备风控身份（UMID），"
                      "活动窗口每天 10:00（UTC+8）开启")
            ok = False
        else:
            ok = False
            detail = "当前没有可领取的签到活动（活动窗口每天 10:00 开启，若已领过则活动行消失）"
    else:
        cid = _first_text(target.get("campaignId"), target.get("campaignKey"))
        if not cid:
            ok, detail = False, "活动数据缺 campaignId，无法领取"
        else:
            h = _sash_headers(account)
            h["content-type"] = "application/json"
            h["origin"] = _base(account)
            try:
                claim = _request("POST", "{}{}/{}/claim".format(_base(account), CAMPAIGNS_PATH, cid),
                                 h, {})
            except RuntimeError as e:
                return False, str(e)
            body = _json(claim)
            if claim.status_code == 409 or body.get("result") == "ALREADY_CLAIMED" or body.get("replayed") is True:
                detail = "今日已领取"
            elif claim.status_code not in (200, 201):
                ok = False
                detail = "领取失败 HTTP {} {}".format(
                    claim.status_code,
                    _first_text(body.get("errorMessage"), body.get("message"), body.get("msg")))
            elif _first_text(body.get("status")) != "CLAIMED":
                ok = False
                detail = "签到未完成（上游状态 {}）".format(_first_text(body.get("status")) or "未知")
            else:
                benefit = body.get("benefit") if isinstance(body.get("benefit"), dict) else {}
                amount = benefit.get("amount")
                days = ((benefit.get("validity") or {}).get("days")
                        if isinstance(benefit.get("validity"), dict) else None)
                detail = "获得 {} credits".format(amount if amount is not None else "?")
                if days:
                    detail += "，{} 天有效".format(days)

    status = account.setdefault("status", {})
    status.update({"checked_in": bool(ok and (claimed or target is not None)),
                   "checked_at": time.time(), "last_error": "" if ok else detail})
    return ok, detail


def usage(account: dict, cfg: dict) -> dict:
    """用量接口（sash/api/v2/me/usage）需 PAT 交换所得的 jobToken，设备令牌直连不可用。"""
    raise NotImplementedError("Qoder 用量查询需 jobToken，当前仅支持每日签到")
