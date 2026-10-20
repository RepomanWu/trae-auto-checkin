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
def load_accounts() -> list:
    return _read_json(ACCOUNTS_FILE, []) or []


def save_accounts(accounts: list) -> None:
    _atomic_write_json(ACCOUNTS_FILE, accounts)


def find_account(account_id: str):
    for a in load_accounts():
        if a.get("id") == account_id:
            return a
    return None


def upsert_account(account: dict) -> dict:
    """按 uid+host（或 token）去重：已存在则更新，否则新增。返回落盘后的账号。"""
    accounts = load_accounts()
    key = (account.get("uid"), account.get("host"))
    for i, a in enumerate(accounts):
        if a.get("uid") == key[0] and a.get("host") == key[1]:
            # 保留原 id/指纹/创建时间，更新凭证
            for f in ("label", "token", "refresh_token", "region", "expired_at"):
                if account.get(f):
                    a[f] = account[f]
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
