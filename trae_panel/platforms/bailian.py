# -*- coding: utf-8 -*-
"""阿里百炼（DashScope OpenAI 兼容模式，无每日签到）。"""

from __future__ import annotations

from . import _openapi

PROVIDER = {
    "id": "bailian",
    "name": "阿里百炼",
    "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "models": ["qwen-plus", "qwen-turbo", "qwen-max", "qwen3-coder-plus"],
}

import_account, checkin, usage = _openapi.binding(PROVIDER)
