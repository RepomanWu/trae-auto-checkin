# -*- coding: utf-8 -*-
"""服务配置：默认值 <- data/config.json <- 环境变量。

面板里改的配置会落盘到 data/config.json；环境变量用于 Docker 部署时注入（如访问密钥、端口）。
"""

from __future__ import annotations

import json
import os
from copy import deepcopy
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("TRAE_PANEL_DATA_DIR") or (BASE_DIR / "data")).resolve()
CONFIG_FILE = DATA_DIR / "config.json"
ACCOUNTS_FILE = DATA_DIR / "accounts.json"
HISTORY_FILE = DATA_DIR / "history.json"
LOG_FILE = DATA_DIR / "panel.log"

DEFAULT_CONFIG = {
    # 面板访问密钥（key）：留空表示不校验（仅建议内网/本机使用）
    "admin_key": "",
    "listen": {"host": "0.0.0.0", "port": 7864},
    # 签到来源：1=Trae CN(IDE/Work)，2=SOLO/企业版
    "req_source": 1,
    # 可选：强制覆盖账号自带的 region（一般留空，跟随登录态）
    "region_override": "",
    # 出站指纹默认值（服务器无本机客户端，用稳定默认值；账号级 device/machine 在导入时生成并持久化）
    "device": {
        "ide_version": "3.3.67",
        "ide_version_code": "20260401",
        "device_type": "linux",
        "os_version": "Linux",
    },
    "schedule": {
        "checkin_enabled": True,
        "checkin_times": ["08:05"],          # 每天签到时刻（HH:MM，可多个）
        "usage_enabled": True,
        "usage_interval_minutes": 60,        # 余额/用量自动刷新间隔（分钟）
        "timezone": "Asia/Shanghai",
    },
    "notify": {
        "enabled": False,
        "email": {
            "enabled": False, "smtp_host": "", "smtp_port": 465, "use_ssl": True,
            "from": "", "auth_code": "", "to": [],
        },
        "wecom": {"enabled": False, "webhook_url": ""},
    },
    # OpenAI 兼容网关：把账号对话能力暴露为 /v1/chat/completions
    "gateway": {
        "enabled": True,
        "api_key": "",                                  # 网关访问密钥（key），留空则不校验
        "infer_host": "https://trae-api-cn.mchost.guru",
        "intent": "explain_code",                       # 上游 intent_name
        "default_model": "",                            # 未匹配到模型时的兜底
        "model_aliases": {},                            # 客户端模型名 -> 上游 model_name
        "timeout": 300,
    },
    # 用量/签到历史保留天数
    "history_keep_days": 90,
}


def _deep_merge(base, override):
    """把 override 深度并入 base（就地修改并返回 base）。"""
    if isinstance(override, dict) and isinstance(base, dict):
        for k, v in override.items():
            base[k] = _deep_merge(base.get(k), v)
        return base
    return override


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    cfg = deepcopy(DEFAULT_CONFIG)
    ensure_data_dir()
    if CONFIG_FILE.exists():
        try:
            cfg = _deep_merge(cfg, json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    env = os.environ
    if env.get("TRAE_PANEL_ADMIN_KEY"):
        cfg["admin_key"] = env["TRAE_PANEL_ADMIN_KEY"].strip()
    if env.get("TRAE_PANEL_HOST"):
        cfg["listen"]["host"] = env["TRAE_PANEL_HOST"].strip()
    if env.get("TRAE_PANEL_PORT"):
        try:
            cfg["listen"]["port"] = int(env["TRAE_PANEL_PORT"])
        except ValueError:
            pass
    if env.get("TZ"):
        cfg["schedule"]["timezone"] = env["TZ"].strip()
    return cfg


def save_config(patch: dict) -> dict:
    """深合并补丁并原子落盘，返回合并后的完整配置。"""
    merged = _deep_merge(load_config(), patch or {})
    ensure_data_dir()
    tmp = CONFIG_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CONFIG_FILE)
    return merged
