# -*- coding: utf-8 -*-
"""小米 MiMo（OpenAI 兼容上游，无每日签到）。"""

from __future__ import annotations

from . import _openapi

PROVIDER = {
    "id": "mimo",
    "name": "小米 MiMo",
    "base_url": "https://api.xiaomimimo.com/v1",
    "models": ["mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.5-pro"],
}

import_account, checkin, usage = _openapi.binding(PROVIDER)
