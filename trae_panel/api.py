# -*- coding: utf-8 -*-
"""Trae 官方接口调用（签到 / 领取 / 用量），带 401 自动刷新与瞬时错误重试。"""

from __future__ import annotations

import json
import time

import requests

from .crypto import DEFAULT_HOST, build_headers, refresh_access_token

MAX_RETRY = 2
RETRY_DELAY = 3


class AuthExpired(Exception):
    """登录态失效且无法刷新，需要重新导入账号。"""


def _unwrap(resp: dict) -> dict:
    """兼容 {checked_in:...} 与 {code:0, data:{checked_in:...}} 两种返回。"""
    if isinstance(resp, dict) and "checked_in" not in resp \
            and isinstance(resp.get("data"), dict) and "checked_in" in resp["data"]:
        return resp["data"]
    return resp


class TraeClient:
    """针对单个账号的接口客户端；token 刷新后就地更新 account（由调用方决定是否落盘）。"""

    def __init__(self, account: dict, device_cfg: dict):
        self.account = account
        self.device_cfg = device_cfg or {}
        self.host = (account.get("host") or DEFAULT_HOST).rstrip("/")

    def _refresh(self) -> bool:
        token, new_refresh = refresh_access_token(
            self.host, self.account.get("uid"), self.account.get("refresh_token"))
        if token:
            self.account["token"] = token
            if new_refresh:
                self.account["refresh_token"] = new_refresh
            return True
        return False

    def post(self, path: str, body: dict | None = None, timeout: int = 30) -> dict:
        url = self.host + path
        last = None
        for _ in range(MAX_RETRY + 1):
            headers = build_headers(self.account, self.device_cfg)
            try:
                r = requests.post(url, headers=headers, json=body or {}, timeout=timeout)
            except requests.RequestException as e:
                last = str(e)
                time.sleep(RETRY_DELAY)
                continue
            if r.status_code == 401:
                if self._refresh():
                    continue
                raise AuthExpired("登录态已失效且刷新失败，请重新导入该账号")
            if r.status_code in (502, 503, 429):
                last = "HTTP {}".format(r.status_code)
                time.sleep(RETRY_DELAY)
                continue
            try:
                data = r.json()
            except ValueError:
                raise RuntimeError("HTTP {}: {}".format(
                    r.status_code, r.content.decode("utf-8", "replace")[:200]))
            if r.status_code >= 400:
                raise RuntimeError("HTTP {}: {}".format(
                    r.status_code, json.dumps(data, ensure_ascii=False)[:200]))
            return data
        raise RuntimeError("请求失败（重试 {} 次）：{}".format(MAX_RETRY, last))


# ---- 具体接口 ----
def checkin_status(client: TraeClient, req_source: int) -> dict:
    return _unwrap(client.post("/trae/api/v2/ug/checkin_credits/status",
                               {"req_source": req_source}))


def checkin_claim(client: TraeClient, req_source: int) -> dict:
    return client.post("/trae/api/v2/ug/checkin_credits/claim", {"req_source": req_source})


def user_usage(client: TraeClient, req_source: int) -> dict:
    """积分/额度用量：usage_summary(总量/已消耗/比例) + 积分包列表。"""
    return client.post("/trae/api/v2/pay/ide_user_ent_usage",
                       {"require_usage": True, "req_source": req_source})
