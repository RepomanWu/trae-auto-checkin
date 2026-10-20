# -*- coding: utf-8 -*-
"""火山方舟（OpenAI 兼容上游，无每日签到）。

方舟模型走「推理接入点 ID」或新版模型 ID（以控制台为准），
默认不预置模型名——导入时用 JSON 指定 models：
    {"api_key": "...", "models": ["doubao-seed-1-6-250615"]}
"""

from __future__ import annotations

from . import _openapi

PROVIDER = {
    "id": "volcengine",
    "name": "火山方舟",
    "base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "models": [],
}

import_account, checkin, usage = _openapi.binding(PROVIDER)
