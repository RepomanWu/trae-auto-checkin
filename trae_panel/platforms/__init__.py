# -*- coding: utf-8 -*-
"""多平台注册表。

一个平台 = 一条元数据 + （可选）一个适配器模块。
实现了的平台在 `trae_panel/platforms/<id>.py` 里提供：

    import_account(raw, label) -> dict     # 解析本机导出内容，返回账号 dict
    checkin(account, cfg) -> (ok, detail)  # 每日签到
    usage(account, cfg) -> dict            # 用量/余额快照
    run_task(task_id, account, cfg)        # 其他任务（可选）

注册表会自动发现这些模块；只有元数据、没有适配器的平台在面板里显示为「待接入」。

账号 kind：
  client —— 客户端登录态（本机导出 → 面板导入），通常有每日签到/任务
  api    —— 只是 API Key（OpenAI 兼容上游），没有签到，用于网关转发
"""

from __future__ import annotations

import importlib

# 任务文案（任务中心展示用）
TASK_LABELS = {
    "checkin": "每日签到",
    "usage": "用量/积分快照",
    "travel": "派猫旅行",
    "idle": "活跃点亮",
    "tasks": "成长任务",
}

PLATFORMS = [
    {
        "id": "trae",
        "name": "Trae CN",
        "kind": "client",
        "gateway": True,
        "login": "读取本机 Trae 客户端登录态（storage.json / 导出脚本）",
        "import_hint": "粘贴整份 storage.json，或 export_local.py / export_local.ps1 输出的 JSON",
        "tasks": ["checkin", "usage"],
        "note": "已实现：每日签到 + 用量/积分监控 + OpenAI 兼容网关",
    },
    {
        "id": "workbuddy",
        "name": "WorkBuddy / CodeBuddy（腾讯）",
        "kind": "client",
        "gateway": True,
        "login": "本机从 WorkBuddy 客户端导出凭据（5.6+ 为加密凭据，解密密钥来自其 Electron，必须本机）",
        "import_hint": "在本机导出 WorkBuddy 凭据后粘贴",
        "tasks": ["checkin", "travel", "idle", "tasks"],
        "note": "待接入：每日签到（加油站）+ 派猫旅行 + 活跃点亮 + 成长任务",
    },
    {
        "id": "minimax",
        "name": "MiniMax Code",
        "kind": "client",
        "gateway": False,
        "login": "本机导出 MiniMax Code 登录态",
        "import_hint": "在本机导出登录态后粘贴",
        "tasks": ["checkin", "usage"],
        "note": "待接入：每日签到 400 积分（第 4/7 天 1000，一周 4000）",
    },
    {
        "id": "qoder",
        "name": "Qoder（通义灵码）",
        "kind": "client",
        "gateway": False,
        "login": "本机导出 Qoder 登录态 / PAT",
        "import_hint": "在本机导出登录态后粘贴",
        "tasks": ["checkin", "usage"],
        "note": "待接入：每天 10 点领 100 积分",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek 官方",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填入 API Key（api.deepseek.com）",
        "tasks": [],
        "note": "无每日签到（只有新用户一次性赠送），可作网关上游",
    },
    {
        "id": "mimo",
        "name": "小米 MiMo",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填入 API Key（api.xiaomimimo.com/v1）",
        "tasks": [],
        "note": "无每日签到，可作网关上游",
    },
    {
        "id": "qianfan",
        "name": "百度千帆",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填入 API Key（千帆 OpenAI 兼容端点）",
        "tasks": [],
        "note": "免费试用大模型，可作网关上游",
    },
    {
        "id": "bailian",
        "name": "阿里百炼",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填入 API Key（dashscope 兼容模式）",
        "tasks": [],
        "note": "免费试用大模型，可作网关上游",
    },
    {
        "id": "volcengine",
        "name": "火山方舟",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填入 API Key（ark.cn-beijing.volces.com/api/v3）",
        "tasks": [],
        "note": "免费试用大模型，可作网关上游",
    },
]

_BY_ID = {p["id"]: p for p in PLATFORMS}


def all_platforms() -> list:
    """返回所有平台元数据（带 implemented 标记）。"""
    out = []
    for p in PLATFORMS:
        item = dict(p)
        item["implemented"] = adapter(p["id"]) is not None
        item["task_labels"] = [TASK_LABELS.get(t, t) for t in p.get("tasks", [])]
        out.append(item)
    return out


def get(platform_id: str) -> dict:
    return _BY_ID.get(platform_id) or _BY_ID["trae"]


def exists(platform_id: str) -> bool:
    return platform_id in _BY_ID


def adapter(platform_id: str):
    """返回平台的适配器模块；没有实现则返回 None。

    新增平台：在本包下加 `<id>.py`，提供 import_account/checkin/usage 即可，
    注册表与面板会自动发现。
    """
    if platform_id == "trae":
        return _TRAE_SHIM
    try:
        return importlib.import_module("{}.{}".format(__name__, platform_id))
    except ImportError:
        return None


class _TraeShim:
    """Trae 的实现仍在 service.py（历史原因），这里只做转发，避免循环依赖。"""

    @staticmethod
    def _svc():
        from .. import service
        return service

    @staticmethod
    def import_account(raw, label=""):
        return _TraeShim._svc().import_account(raw, label)

    @staticmethod
    def checkin(account, cfg):
        return _TraeShim._svc()._checkin_one(account, cfg, cfg.get("device") or {},
                                             cfg.get("req_source", 1))

    @staticmethod
    def usage(account, cfg):
        from ..api import TraeClient
        return _TraeShim._svc()._snapshot_usage(account, TraeClient(account, cfg.get("device") or {}),
                                                cfg.get("req_source", 1))


_TRAE_SHIM = _TraeShim()
