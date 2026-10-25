# -*- coding: utf-8 -*-
"""账号与历史记录的本地持久化（原子写入）。

说明：账号文件 accounts.json 内含明文 accessToken / refreshToken（服务端需用它调用官方
接口）。请勿把该目录提交到公开仓库，Docker 部署时务必只挂载到你可控的卷。
"""

from __future__ import annotations

import json
import os
import secrets
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import ACCOUNTS_FILE, HISTORY_FILE, ensure_data_dir


def _atomic_write_json(path: Path, data) -> None:
    ensure_data_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


# ---- 账号 ----
def load_accounts(platform: str | None = None) -> list:
    """读取账号；旧数据没有 platform 字段时按 trae 处理。可按平台过滤。"""
    accounts = _read_json(ACCOUNTS_FILE, []) or []
    for a in accounts:
        a.setdefault("platform", "trae")
    if platform:
        accounts = [a for a in accounts if a.get("platform") == platform]
    return accounts


def save_accounts(accounts: list) -> None:
    _atomic_write_json(ACCOUNTS_FILE, accounts)


def find_account(account_id: str):
    for a in load_accounts():
        if a.get("id") == account_id:
            return a
    return None


def upsert_account(account: dict) -> dict:
    """按 platform+uid+host（或 token）去重：已存在则更新，否则新增。返回落盘后的账号。"""
    accounts = load_accounts()
    account.setdefault("platform", "trae")
    key = (account.get("platform"), account.get("uid"), account.get("host"))
    for i, a in enumerate(accounts):
        if (a.get("platform"), a.get("uid"), a.get("host")) == key:
            # 保留原 id/指纹/创建时间，更新凭证
            for f in ("token", "refresh_token", "region", "expired_at",
                      "domain", "enterprise_id", "device_token"):
                if account.get(f):
                    a[f] = account[f]
            # 备注名：只有「用户显式填写」或「原来没有」时才覆盖，
            # 否则重新导入同一账号会把手动改过的备注冲掉。
            if account.get("label") and (account.get("label_explicit") or not a.get("label")):
                a["label"] = account["label"]
            # 只有来自客户端真实指纹的导入才覆盖设备信息（避免随机指纹盖掉真实值）
            if account.get("from_client"):
                for f in ("device_id", "machine_id", "ide_version", "ide_version_code",
                          "device_type", "os_version", "from_client"):
                    if account.get(f):
                        a[f] = account[f]
            a["updated_at"] = int(time.time())
            accounts[i] = a
            save_accounts(accounts)
            return a
    account.setdefault("id", secrets.token_hex(6))
    account.setdefault("created_at", int(time.time()))
    account["updated_at"] = int(time.time())
    account.setdefault("status", {})
    accounts.append(account)
    save_accounts(accounts)
    return account


def delete_account(account_id: str) -> bool:
    accounts = load_accounts()
    remain = [a for a in accounts if a.get("id") != account_id]
    if len(remain) == len(accounts):
        return False
    save_accounts(remain)
    return True


def set_account_paused(account_id: str, paused: bool):
    """暂停/恢复账号：暂停只影响网关选号（不再消耗该号 token），定时任务照常执行。"""
    accounts = load_accounts()
    for a in accounts:
        if a.get("id") == account_id:
            a["paused"] = bool(paused)
            a["paused_at"] = int(time.time()) if paused else 0
            save_accounts(accounts)
            return a
    return None


def update_account_remark(account_id: str, label=None, note=None):
    """更新账号备注：label 为列表里显示的短名，note 为自由备注（都可留空不改）。"""
    accounts = load_accounts()
    for a in accounts:
        if a.get("id") == account_id:
            if label is not None and str(label).strip():
                a["label"] = str(label).strip()
            if note is not None:
                a["note"] = str(note).strip()
            a["remark_at"] = int(time.time())
            save_accounts(accounts)
            return a
    return None


# ---- 历史 ----
def load_history() -> dict:
    return _read_json(HISTORY_FILE, {"checkins": [], "snapshots": []})


def append_checkin(event: dict) -> None:
    hist = load_history()
    hist.setdefault("checkins", []).append(event)
    _atomic_write_json(HISTORY_FILE, hist)


def append_snapshot(snap: dict) -> None:
    hist = load_history()
    hist.setdefault("snapshots", []).append(snap)
    _atomic_write_json(HISTORY_FILE, hist)


def prune_history(keep_days: int) -> None:
    """按保留天数裁剪历史记录。"""
    if not keep_days or keep_days <= 0:
        return
    cutoff = datetime.now() - timedelta(days=keep_days)
    hist = load_history()

    def keep(item):
        raw = item.get("ts")
        try:
            return datetime.fromtimestamp(float(raw)) >= cutoff
        except (TypeError, ValueError):
            return True

    hist["checkins"] = [x for x in hist.get("checkins", []) if keep(x)]
    hist["snapshots"] = [x for x in hist.get("snapshots", []) if keep(x)]
    _atomic_write_json(HISTORY_FILE, hist)
