# -*- coding: utf-8 -*-
"""百度千帆（OpenAI 兼容上游，无每日签到）。

兼容端点：https://qianfan.baidubce.com/v2 → /chat/completions
具体可用模型名以控制台为准，可用 JSON 导入时覆盖 models。
"""

from __future__ import annotations

from . import _openapi

PROVIDER = {
    "id": "qianfan",
    "name": "百度千帆",
    "base_url": "https://qianfan.baidubce.com/v2",
    "models": ["ernie-4.5-turbo-128k", "ernie-4.5-8k-preview", "ernie-speed-128k"],
}

import_account, checkin, usage = _openapi.binding(PROVIDER)
