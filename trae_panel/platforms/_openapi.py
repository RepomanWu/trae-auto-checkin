# -*- coding: utf-8 -*-
"""API 型平台的公共适配器工厂（OpenAI 兼容上游）。

这类平台没有每日签到/成长任务，只是「一个 base_url + 一个 API Key」，
用于把官方 API 作为面板网关（/v1/chat/completions）的上游。

账号导入支持三种粘贴内容：
  1. 只填 API Key
  2. `base_url|API Key`（覆盖默认 base_url）
  3. JSON：{"api_key": "...", "base_url": "...", "models": ["..."]}

`binding(provider)` 返回 (import_account, checkin, usage)，供 platforms/<id>.py 使用。
"""

from __future__ import annotations

import json


def binding(provider: dict):
    """按 provider 元数据（id/name/base_url/models）生成适配器函数组。"""

    def import_account(raw: str, label: str = "") -> dict:
        text = (raw or "").strip()
        if not text:
            raise ValueError("请填写 API Key")
        api_key = ""
        base_url = provider["base_url"]
        models = list(provider.get("models") or [])
        if text.startswith("{"):
            try:
                parsed = json.loads(text)
            except ValueError:
                raise ValueError("凭据不是合法 JSON")
            if isinstance(parsed, list):
                parsed = parsed[0] if parsed else {}
            if not isinstance(parsed, dict):
                raise ValueError("凭据格式异常")
            api_key = str(parsed.get("api_key") or parsed.get("key") or
                          parsed.get("token") or "").strip()
            if parsed.get("base_url"):
                base_url = str(parsed["base_url"]).strip()
            if isinstance(parsed.get("models"), list) and parsed["models"]:
                models = [str(m) for m in parsed["models"]]
        elif "|" in text:
            left, right = text.split("|", 1)
            base_url, api_key = left.strip(), right.strip()
        else:
            api_key = text
        if not api_key:
            raise ValueError("未识别到 API Key")
        return {
            "platform": provider["id"],
            "host": base_url.rstrip("/"),
            "uid": api_key[-8:],
            "label": (label or "").strip() or provider["name"],
            "token": api_key,
            "models": models,
        }

    def checkin(account: dict, cfg: dict):
        raise NotImplementedError("API 型平台没有每日签到")

    def usage(account: dict, cfg: dict):
        raise NotImplementedError("API 型平台没有用量接口")

    return import_account, checkin, usage
