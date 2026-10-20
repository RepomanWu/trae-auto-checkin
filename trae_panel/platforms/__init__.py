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
        "gateway": False,
        "login": "本机从 WorkBuddy 客户端/插件导出凭据（auth JSON，含 accessToken / refreshToken / uid）",
        "import_hint": "粘贴本机导出的 WorkBuddy auth JSON（嵌套形或扁平形均可）",
        "tasks": ["checkin", "usage", "travel", "idle"],
        "note": "已实现：每日签到 + 余额/积分 + 派猫猫旅行 + 活跃点亮/连登兑换/抽奖",
    },
    {
        "id": "minimax",
        "name": "MiniMax Code",
        "kind": "client",
        "gateway": False,
        "login": "本机 MiniMax Code 的 minimax-agent-config.json（含 tokens.accessToken）",
        "import_hint": "粘贴 minimax-agent-config.json 内容，或直接粘贴 accessToken（JWT）",
        "tasks": ["checkin", "usage"],
        "note": "已实现：每日签到 400 积分（第 4/7 天 1000，一周 4000）+ 配额/剩余积分",
    },
    {
        "id": "qoder",
        "name": "Qoder（通义灵码）",
        "kind": "client",
        "gateway": False,
        "login": "本机设备授权登录后导出 access_token(dt-)/refresh_token(drt-)（国际版还需设备风控三件套）",
        "import_hint": "粘贴 access_token，或 access_token|refresh_token；国际版粘贴含 region/machine_token 等的 JSON",
        "tasks": ["checkin"],
        "note": "已实现：每日签到（每天 10:00 UTC+8 开窗领 100 Credits；国际版需本机 UMID 设备身份）",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek 官方",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填 API Key（默认 https://api.deepseek.com/v1）；也可填 base_url|API Key 或 JSON",
        "tasks": [],
        "note": "无每日签到（仅一次性赠送），作为网关上游：模型 deepseek-chat / deepseek-reasoner",
    },
    {
        "id": "mimo",
        "name": "小米 MiMo",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填 API Key（默认 https://api.xiaomimimo.com/v1）；也可填 base_url|API Key 或 JSON",
        "tasks": [],
        "note": "无每日签到，作为网关上游（OpenAI 兼容）",
    },
    {
        "id": "qianfan",
        "name": "百度千帆",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填 API Key（默认 https://qianfan.baidubce.com/v2）；模型名可用 JSON 覆盖",
        "tasks": [],
        "note": "免费试用大模型，作为网关上游（OpenAI 兼容）",
    },
    {
        "id": "bailian",
        "name": "阿里百炼",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填 API Key（默认 dashscope compatible-mode）；模型名可用 JSON 覆盖",
        "tasks": [],
        "note": "免费试用大模型，作为网关上游（通义千问 OpenAI 兼容模式）",
    },
    {
        "id": "volcengine",
        "name": "火山方舟",
        "kind": "api",
        "gateway": True,
        "login": "API Key",
        "import_hint": "填 API Key（默认 ark api/v3）；方舟需在 JSON 里指定 models（接入点/模型 ID）",
        "tasks": [],
        "note": "免费试用大模型，作为网关上游（需填模型 ID）",
    },
]

_BY_ID = {p["id"]: p for p in PLATFORMS}


# 各平台「添加账号」的详细导入指引（面板渲染用；纯文本，不用 Markdown）。
def _api_guide(platform_name: str, default_url: str, sample: str) -> dict:
    return {
        "steps": [
            "到 {} 开放平台控制台创建 API Key。".format(platform_name),
            "把 API Key 直接粘贴到下方即可（默认端点 {}）。".format(default_url),
            "需要改端点或指定模型时用 JSON：api_key / base_url / models 三个字段。",
        ],
        "format": "API Key ｜ base_url|API Key ｜ JSON（api_key / base_url / models）",
        "sample": sample,
        "placeholder": "粘贴 API Key（或 base_url|API Key / JSON）",
    }


IMPORT_GUIDES = {
    "trae": {
        "steps": [
            "在【已登录 Trae 客户端】的那台本机，用记事本打开："
            "%APPDATA%\\Trae CN\\User\\globalStorage\\storage.json",
            "Ctrl+A 全选、Ctrl+C 复制整份文件（推荐，本机不用装 Python）；"
            "或运行 python export_local.py 拿一行 JSON、没装 Python 时用 "
            "powershell -NoProfile -ExecutionPolicy Bypass -File export_local.ps1",
            "把复制的内容粘贴到下方「凭据内容」，可填备注名，点「导入账号」。",
        ],
        "format": "整份 storage.json（推荐）｜ 导出脚本的一行 JSON ｜ 仅密文串",
        "sample": "",
        "placeholder": "粘贴整份 storage.json，或 export_local.py / export_local.ps1 输出的 JSON",
        "tip": "只有前两种能让网关带上真实设备指纹；仅粘贴密文串时签到可用、但对话网关会被上游限流。",
    },
    "workbuddy": {
        "steps": [
            "在【本机 WorkBuddy 客户端 / 插件】里导出登录凭据（auth JSON），"
            "其中含 accessToken、refreshToken、account.uid、account.nickname。",
            "凭据里的 realm（cn / global）或 domain 会被自动识别，用于选择国内版 / 国际版上游。",
            "把整份 JSON 粘贴到下方「凭据内容」，点「导入账号」。",
        ],
        "format": "嵌套形 {\"auth\":{…},\"account\":{…}} ｜ 扁平形 {\"accessToken\":\"…\",\"uid\":\"…\"}",
        "sample": '{"auth":{"accessToken":"eyJhbGciOiJIUzI1NiJ9.xxxxx.yyy","refreshToken":"eyJhbGciOiJIUzI1NiJ9.aaaa.bbb","expiresAt":1780000000,"domain":"www.codebuddy.cn","realm":"cn"},"account":{"uid":"123456789","enterpriseId":"","nickname":"我的号"},"device_token":""}',
        "placeholder": "粘贴 WorkBuddy auth JSON（嵌套形或扁平形）",
        "tip": "WorkBuddy 桌面端 5.6+ 的本地登录态是加密的，服务器解不开，必须本机导出后粘贴。",
    },
    "minimax": {
        "steps": [
            "在本机找到 MiniMax Code 的配置文件 minimax-agent-config.json"
            "（macOS 位于 ~/Library/Application Support/MiniMax Agent/）。",
            "Ctrl+A 复制整份文件；也可以只复制其中的 tokens.accessToken（JWT）。",
            "把内容粘贴到下方「凭据内容」，点「导入账号」。",
        ],
        "format": "整份 minimax-agent-config.json ｜ 只填 accessToken（JWT）",
        "sample": '{"tokens":{"accessToken":"eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ1c2VyIjp7ImlkIjoiMTIzIn19.sig","refreshToken":""}}',
        "placeholder": "粘贴 minimax-agent-config.json 全文，或 accessToken（JWT）",
        "tip": "签到接口按 JWT 里的 user.id 解析身份，所以直接粘贴 accessToken 也能识别账号。",
    },
    "qoder": {
        "steps": [
            "用官方「设备授权登录」工具拿到 access_token（形如 dt-…）与 refresh_token（形如 drt-…）。",
            "国内版：直接填 access_token，或 access_token|refresh_token（配 refresh 可自动续期）。",
            "国际版：额外需要设备风控三件套，用 JSON 粘贴 machine_token / machine_code / machine_type。",
        ],
        "format": "dt-… ｜ dt-…|drt-… ｜ 国际版 JSON（token / refresh_token / region / machine_*）",
        "sample": "dt-1a2b3c4d5e6f7g8h|drt-9z8y7x6w5v4u3t2s",
        "placeholder": "粘贴 access_token（dt-…）或 access_token|refresh_token（drt-…）",
        "tip": "国际版签到需本机 Qoder 客户端 / CLI 生成的 UMID 设备身份；国内版不需要。",
    },
    "deepseek": _api_guide("DeepSeek", "https://api.deepseek.com/v1",
                           '{"api_key":"sk-你的key","models":["deepseek-chat","deepseek-reasoner"]}'),
    "mimo": _api_guide("小米 MiMo", "https://api.xiaomimimo.com/v1",
                       '{"api_key":"你的key","models":["mimo-v2.6-pro","mimo-v2.6-flash"]}'),
    "qianfan": _api_guide("百度千帆", "https://qianfan.baidubce.com/v2",
                          '{"api_key":"bce-v3/ALTAK-xxxx","models":["ernie-4.5-turbo-128k"]}'),
    "bailian": _api_guide("阿里百炼", "https://dashscope.aliyuncs.com/compatible-mode/v1",
                          '{"api_key":"sk-你的key","models":["qwen-plus","qwen-turbo"]}'),
    "volcengine": {
        "steps": [
            "到火山方舟控制台创建 API Key，并拿到推理接入点 / 模型 ID（形如 ep-… 或 doubao-…）。",
            "因为方舟的模型 ID 因账号而异，必须用 JSON 同时给出 api_key 与 models。",
            "把 JSON 粘贴到下方「凭据内容」，点「导入账号」。",
        ],
        "format": "JSON（api_key / models 必填；可加 base_url）",
        "sample": '{"api_key":"你的key","models":["ep-20260101-abcdef"]}',
        "placeholder": "粘贴 JSON：{\"api_key\":\"…\",\"models\":[\"ep-…\"]}",
        "tip": "默认端点 https://ark.cn-beijing.volces.com/api/v3。",
    },
}


def all_platforms() -> list:
    """返回所有平台元数据（带 implemented 标记与导入指引）。"""
    out = []
    for p in PLATFORMS:
        item = dict(p)
        item["implemented"] = adapter(p["id"]) is not None
        item["task_labels"] = [TASK_LABELS.get(t, t) for t in p.get("tasks", [])]
        item["import_guide"] = IMPORT_GUIDES.get(p["id"]) or {}
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
