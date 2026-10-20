# -*- coding: utf-8 -*-
"""DeepSeek 官方（OpenAI 兼容上游，无每日签到）。"""

from __future__ import annotations

from . import _openapi

PROVIDER = {
    "id": "deepseek",
    "name": "DeepSeek 官方",
    "base_url": "https://api.deepseek.com/v1",
    "models": ["deepseek-chat", "deepseek-reasoner"],
}

import_account, checkin, usage = _openapi.binding(PROVIDER)
