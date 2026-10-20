# -*- coding: utf-8 -*-
"""Trae 登录态（tc 加密格式）解密 + accessToken 刷新 + 出站指纹头。

登录态来自客户端 %APPDATA%/<客户端>/User/globalStorage/storage.json 里的
`iCubeAuthInfo://icube.cloudide`。解密算法逆向自官方客户端，可能随版本变化。
本模块只做内存解密，绝不把明文令牌写盘（落盘由 store.py 单独处理，见其说明）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets

import requests
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

# tc 加密头
HDR = bytes([0x74, 0x63, 0x05, 0x10, 0x00, 0x00])
# 64 字节盐（异或后参与 SHA-512 派生）
SALT_A = bytes([82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251, 124, 227,
                57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203, 84, 123, 148, 50,
                166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78, 8, 46, 161, 102, 40, 217, 36,
                178, 118, 91, 162, 73, 109, 139, 209, 37])
SALT_B = bytes([31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95, 96, 81, 127,
                169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239, 160, 224, 59, 77, 174,
                42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97, 23, 43, 4, 126, 186, 119, 214, 38,
                225, 105, 20, 99, 85, 33, 12, 125])

APP_ID = "6eefa01c-1036-4c7e-9ca5-d891f63bfcd8"
EXCHANGE_CLIENT_ID = "ono9krqynydwx5"
DEFAULT_HOST = "https://api.trae.cn"


def _aes_cbc_decrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return dec.update(data) + dec.finalize()


def decrypt_auth_value(text: str) -> dict:
    """解密 storage.json 中的登录态值（tc 格式），返回含 token/refreshToken/userId 的 dict。

    兼容：明文 JSON（国际版）、带前缀的密文串。
    """
    text = (text or "").strip()
    if text.startswith("iCubeAuthInfo://"):
        text = text[len("iCubeAuthInfo://"):]
    if text.startswith("{"):
        return json.loads(text)

    raw = base64.b64decode(text)
    if len(raw) < 38:
        raise ValueError("登录态长度不足，格式异常")
    hdr, rb, enc = raw[:6], raw[6:38], raw[38:]
    if hdr != HDR:
        raise ValueError("未知的登录态加密格式（header 不匹配）")

    salt = bytes(a ^ b for a, b in zip(SALT_A, SALT_B))
    derived = hashlib.sha512(hashlib.sha512(rb).digest() + salt).digest()
    aes_key, iv = derived[:16], derived[16:32]

    plain = _aes_cbc_decrypt(aes_key, iv, enc)
    # 明文布局：[64 字节 HMAC][JSON 正文][PKCS7 填充]，须先去填充再切分校验
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        plain = plain[:-pad]
    stored, payload = plain[:64], plain[64:]
    if hashlib.sha512(payload).digest() != stored:
        raise ValueError("登录态解密校验失败（SHA-512 不符）")
    return json.loads(payload.decode("utf-8"))


def new_fingerprint() -> dict:
    """为账号生成稳定的 device/machine 指纹（服务器无本机标识，故生成一次并持久化）。"""
    return {"device_id": secrets.token_hex(16), "machine_id": secrets.token_hex(32)}


def refresh_access_token(host: str, uid: str, refresh_token: str, timeout: int = 30):
    """用 refreshToken 换新 accessToken，返回 (token, new_refresh_token|None)；失败返回 (None, None)。"""
    if not refresh_token:
        return None, None
    url = "{}/cloudide/api/v3/trae/oauth/ExchangeToken".format((host or DEFAULT_HOST).rstrip("/"))
    try:
        r = requests.post(url, json={
            "ClientID": EXCHANGE_CLIENT_ID,
            "RefreshToken": refresh_token,
            "ClientSecret": "-",
            "UserID": str(uid or ""),
        }, headers={"Content-Type": "application/json"}, timeout=timeout)
        data = r.json() if r.content else {}
    except Exception:
        return None, None
    result = (data or {}).get("Result") or {}
    return result.get("Token"), result.get("RefreshToken")


def build_headers(account: dict, device_cfg: dict) -> dict:
    """按账号 + 默认指纹拼出站请求头。

    账号若带了真实客户端指纹（device_id / machine_id / ide_version 等）则优先使用；
    对话接口（gateway）对设备指纹敏感，随机指纹会被上游限流。
    """
    token = account.get("token") or ""
    region = account.get("region") or ""
    headers = {
        "Authorization": "Cloud-IDE-JWT " + token,
        "X-Cloudide-Token": token,
        "x-uid": str(account.get("uid") or ""),
        "x-app-id": APP_ID,
        "x-device-id": account.get("device_id") or "",
        "x-machine-id": account.get("machine_id") or "",
        "x-request-id": secrets.token_hex(16),
        "x-ide-version": account.get("ide_version") or device_cfg.get("ide_version") or "3.3.67",
        "x-ide-version-code": account.get("ide_version_code") or device_cfg.get("ide_version_code") or "20260401",
        "x-device-type": account.get("device_type") or device_cfg.get("device_type") or "linux",
        "x-os-version": account.get("os_version") or device_cfg.get("os_version") or "Linux",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if region:
        headers["X-User-Region"] = region
    return headers


def device_info_from_storage(storage: dict) -> dict:
    """从客户端 storage.json 中提取对话接口所需的真实设备信息。"""
    import re as _re
    machine_id = storage.get("telemetry.machineId") or ""
    device_id = ""
    for key in storage:
        m = _re.match(r"^iCubeAuthInfo://icube-dc:(\d+)$", key)
        if m:
            device_id = m.group(1)
            break
    version = (storage.get("iCubeLastVersion") or "").strip()
    return {
        "machine_id": machine_id,
        "device_id": device_id,
        "ide_version": version,
        "ide_version_code": version.replace(".", "") if version else "",
        "device_type": "windows",
        "os_version": "Windows",
    }


def account_label(auth: dict, fallback: str) -> str:
    """给账号取一个可读名字：用户名 -> 手机掩码 -> 目录名。"""
    acct = auth.get("account") or {}
    return acct.get("username") or acct.get("nonPlainTextMobile") or fallback


def account_region(auth: dict) -> str:
    region = auth.get("userRegion") or {}
    if isinstance(region, str):
        return region
    return region.get("region") or region.get("_aiRegion") or ""
