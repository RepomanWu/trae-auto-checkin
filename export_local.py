# -*- coding: utf-8 -*-
"""本机登录态导出工具（在装有 Trae 客户端的机器上运行）。

把本机各 Trae 客户端的登录态密文导出为 trae_export.json，用于粘贴/上传到服务器面板。
导出文件含密文（可解出 accessToken），属敏感信息，请勿外传，用完即删。

用法：
    python export_local.py
    python export_local.py --app-dirs "Trae CN,TRAE SOLO CN"
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from trae_panel.crypto import account_label, decrypt_auth_value, device_info_from_storage

AUTH_KEY = "iCubeAuthInfo://icube.cloudide"
DEFAULT_DIRS = ["Trae CN", "TRAE SOLO CN", "Trae", "TRAE SOLO"]


def scan(app_dirs):
    appdata = os.environ.get("APPDATA", "")
    roots = [Path(appdata)] if appdata else []
    # macOS 兜底
    roots.append(Path.home() / "Library" / "Application Support")
    out, seen = [], set()
    for root in roots:
        for name in app_dirs:
            p = root / name / "User" / "globalStorage" / "storage.json"
            if not p.exists():
                continue
            try:
                storage = json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                print("跳过 {}：读取失败 {}".format(name, e))
                continue
            enc = storage.get(AUTH_KEY)
            if not enc:
                print("跳过 {}：无登录态".format(name))
                continue
            key = str(p).lower()
            if key in seen:
                continue
            seen.add(key)
            entry = {"app_dir": name, "blob": enc}
            entry.update(device_info_from_storage(storage))
            try:
                auth = decrypt_auth_value(enc)
                entry["label"] = account_label(auth, name)
                entry["uid"] = str(auth.get("userId") or "")
            except Exception as e:
                entry["label"] = name
                entry["decrypt_error"] = str(e)
            out.append(entry)
    return out


def main():
    ap = argparse.ArgumentParser(description="导出本机 Trae 登录态用于服务器面板导入")
    ap.add_argument("--app-dirs", default=",".join(DEFAULT_DIRS),
                    help="客户端应用数据目录，逗号分隔")
    ap.add_argument("-o", "--out", default="trae_export.json", help="输出文件")
    args = ap.parse_args()

    entries = scan([x.strip() for x in args.app_dirs.split(",") if x.strip()])
    if not entries:
        print("未找到任何登录态。请先打开 Trae 客户端登录一次，或用 --app-dirs 指定目录。")
        return 1
    out = Path(args.out).resolve()
    out.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已导出 {} 个账号 -> {}".format(len(entries), out))
    for e in entries:
        print("  - {}（uid={}）{}".format(e.get("label"), e.get("uid"),
                                         "解密失败：" + e["decrypt_error"] if e.get("decrypt_error") else ""))
        print("    密文（可粘贴到面板添加账号）：")
        print("    " + e["blob"])
    print("\n注意：该文件含登录态密文（可解出令牌），请勿外传，导入后删除。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
