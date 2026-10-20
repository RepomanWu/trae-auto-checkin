# -*- coding: utf-8 -*-
"""
Trae CN / TraeCode 每日自动签到领积分（HTTP API 版，可跨机器复制）

原理：不打开客户端、不模拟点击，直接读取 Trae CN 客户端本地保存的登录态
（%APPDATA%\\Trae CN\\User\\globalStorage\\storage.json），解出 token 后调用
官方签到接口 api.trae.cn 完成每日签到并领取积分。

- 只读登录态，不修改任何文件
- 不打印 token / 不泄露敏感信息
- 自动扫描多个应用数据目录并按 token 去重（兼容多账号，按账号分别通知）
- 结果写入脚本同目录的 checkin.log
- 支持邮件（SMTP）+ 企业微信（webhook）两种通知渠道，可在 config.json 中独立开关
- 配置独立放在 config.json；命令行可用 --no-notify 临时关闭通知
- 依赖：requests、cryptography（邮件走标准库 smtplib）

用法：
    python checkin_trae.py            # 查询并签到（默认），结束后发通知
    python checkin_trae.py --status   # 只查询签到状态，不领取、不发通知
    python checkin_trae.py --no-notify# 签到但不发通知
"""

import base64
import hashlib
import json
import os
import re
import smtplib
import sys
import time
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText
from pathlib import Path

try:
    import requests
except ImportError:
    print("缺少依赖 requests，请先执行：python -m pip install requests cryptography")
    sys.exit(1)

# ---- AES 解密实现（优先 cryptography，回退 pycryptodome） ----
try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    def _aes_cbc_decrypt(key, iv, data):
        dec = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
        return dec.update(data) + dec.finalize()

except ImportError:
    from Crypto.Cipher import AES

    def _aes_cbc_decrypt(key, iv, data):
        return AES.new(key, AES.MODE_CBC, iv).decrypt(data)

# ---- 常量 ----
AUTH_KEY = "iCubeAuthInfo://icube.cloudide"
API_BASE = "https://api.trae.cn/trae/api/v2/ug/checkin_credits"
CONFIG_NAME = "config.json"

# iCube 登录态解密常量（与 Trae 客户端加密格式一致）
HDR_LEN = 6
KEY_LEN = 32
HMAC_LEN = 64
URE = bytes([82, 9, 106, 213, 48, 54, 165, 56, 191, 64, 163, 158, 129, 243, 215, 251, 124, 227, 57, 130, 155, 47, 255, 135, 52, 142, 67, 68, 196, 222, 233, 203, 84, 123, 148, 50, 166, 194, 35, 61, 238, 76, 149, 11, 66, 250, 195, 78, 8, 46, 161, 102, 40, 217, 36, 178, 118, 91, 162, 73, 109, 139, 209, 37])
DRE = bytes([31, 221, 168, 51, 136, 7, 199, 49, 177, 18, 16, 89, 39, 128, 236, 95, 96, 81, 127, 169, 25, 181, 74, 13, 45, 229, 122, 159, 147, 201, 156, 239, 160, 224, 59, 77, 174, 42, 245, 176, 200, 235, 187, 60, 131, 83, 153, 97, 23, 43, 4, 126, 186, 119, 214, 38, 225, 105, 20, 99, 85, 33, 12, 125])

BASE_DIR = Path(__file__).resolve().parent
LOG_FILE = BASE_DIR / "checkin.log"
MAX_RETRY = 2          # 网络/接口重试次数
RETRY_DELAY = 3        # 重试间隔秒

# ---- 配置加载 ----
DEFAULT_CONFIG = {
    "checkin": {"req_source": 1, "app_dirs": ["Trae CN"], "extra_paths": [],
                "device_id_fallback": ""},
    "notify": {"enabled": False, "email": {"enabled": False}, "wecom": {"enabled": False}},
}


def config_path() -> Path:
    arg = os.environ.get("TRAE_CHECKIN_CONFIG")
    if arg:
        return Path(arg).expanduser()
    return BASE_DIR / CONFIG_NAME


def load_config() -> dict:
    """读取 config.json；缺失/损坏时回退默认配置（通知关闭）并给出提示"""
    cfg = dict(DEFAULT_CONFIG)
    path = config_path()
    if not path.exists():
        log("WARN: 未找到配置文件 {}，已使用默认配置（通知关闭）".format(path))
        return cfg
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # 深合并：把用户给的字段覆盖到默认结构上
        def merge(base, override):
            if isinstance(override, dict) and isinstance(base, dict):
                for k, v in override.items():
                    base[k] = merge(base.get(k), v)
                return base
            return override
        cfg = merge(cfg, data)
        return cfg
    except Exception as e:
        log("WARN: 配置文件解析失败（{}），已使用默认配置（通知关闭）".format(e))
        return cfg


def cfg_checkin(cfg: dict) -> dict:
    return cfg.get("checkin") or {}


def cfg_notify(cfg: dict) -> dict:
    return cfg.get("notify") or {}


def log(msg: str) -> None:
    line = "[{}] {}".format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg)
    try:
        print(line)
    except Exception:
        pass
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError as e:
        print("WARN: 写入日志失败：{}".format(e))


# ---- 登录态解析 ----
def decrypt_auth(b64_text: str) -> dict:
    """解密 iCubeAuthInfo 加密串，返回含 token / userRegion 的 dict"""
    t = base64.b64decode(b64_text)
    key = t[HDR_LEN:HDR_LEN + KEY_LEN]
    sha = hashlib.sha512(key).digest()
    xor = bytes(a ^ b for a, b in zip(URE, DRE))
    h = hashlib.sha512(sha + xor).digest()
    aes_key, iv = h[:16], h[16:32]
    ct = t[HDR_LEN + KEY_LEN:]
    plain = _aes_cbc_decrypt(aes_key, iv, ct)
    pad = plain[-1] if plain else 0
    if 1 <= pad <= 16 and plain[-pad:] == bytes([pad]) * pad:
        plain = plain[:-pad]
    return json.loads(plain[HMAC_LEN:].decode("utf-8"))


def account_label(auth: dict, fallback: str) -> str:
    """给账号一个可区分的名字：优先用户名，其次手机号掩码，最后目录名"""
    acct = auth.get("account") or {}
    if acct.get("username"):
        return acct["username"]
    if acct.get("nonPlainTextMobile"):
        return acct["nonPlainTextMobile"]
    return fallback


def scan_targets(app_dirs, extra_paths):
    """扫描 [(名称, storage.json 路径, 应用数据目录)]，按路径去重"""
    appdata = os.environ.get("APPDATA", "")
    targets = []
    for n in app_dirs:
        targets.append((n, Path(appdata) / n / "User" / "globalStorage" / "storage.json",
                        Path(appdata) / n))
    for raw in extra_paths:
        p = Path(os.path.expandvars(raw)).expanduser()
        if p.suffix.lower() == ".json":
            app_dir = p.parents[2] if len(p.parents) > 2 else p.parent
            targets.append((p.parent.name, p, app_dir))
        else:
            targets.append((p.name, p / "User" / "globalStorage" / "storage.json", p))
    seen, uniq = set(), []
    for name, sf, d in targets:
        key = str(sf).lower()
        if key not in seen:
            seen.add(key)
            uniq.append((name, sf, d))
    return uniq


def load_accounts(app_dirs, extra_paths):
    """从各客户端 storage.json 读取账号，按 token 去重"""
    accounts, seen = [], set()
    for name, path, _ in scan_targets(app_dirs, extra_paths):
        try:
            if not path.exists():
                log("跳过 [{}]：未找到 {}".format(name, path))
                continue
            storage = json.loads(path.read_text(encoding="utf-8"))
            enc = storage.get(AUTH_KEY)
            if not enc:
                log("跳过 [{}]：storage.json 中无登录态".format(name))
                continue
            auth = decrypt_auth(enc)
            token = auth.get("token")
            if not token:
                log("ERROR: [{}] 登录态中无 token，请打开 Trae CN 重新登录".format(name))
                continue
            if token in seen:
                continue
            seen.add(token)
            accounts.append((name, auth))
        except Exception as e:
            log("ERROR: 解析 [{}] 失败：{}".format(name, e))
    return accounts


def load_device_id(app_dirs, extra_paths, fallback: str) -> str:
    """设备 ID：ahanet 配置 -> 客户端日志 -> 兜底常量"""
    dirs = [d for _, _, d in scan_targets(app_dirs, extra_paths)]
    for d in dirs:
        p = d / "ahanet" / "tt_net_config.config"
        try:
            m = re.search(r"device_id[&#*]*(\d+)", p.read_text(encoding="utf-8", errors="ignore"))
            if m:
                return m.group(1)
        except OSError:
            continue
    for d in dirs:
        logs_dir = d / "logs"
        if not logs_dir.is_dir():
            continue
        try:
            main_logs = sorted(logs_dir.glob("*/main.log"),
                               key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            continue
        for p in main_logs[:5]:
            try:
                m = re.search(r"\[ICDRS\].*?did: (\d+)", p.read_text(encoding="utf-8", errors="ignore"))
                if m:
                    return m.group(1)
            except OSError:
                continue
    return fallback


# ---- 签到 API ----
def api(url: str, token: str, region: str, device_id: str, req_source: int) -> dict:
    headers = {
        "Authorization": "Cloud-IDE-JWT " + token,
        "Content-Type": "application/json",
        "x-device-id": device_id,
    }
    if region:
        headers["X-User-Region"] = region
    last = None
    for attempt in range(MAX_RETRY + 1):
        try:
            r = requests.post(url, headers=headers, json={"req_source": req_source}, timeout=30)
            if r.status_code in (502, 503):
                last = "HTTP {}".format(r.status_code)
                time.sleep(RETRY_DELAY)
                continue
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            last = "HTTP {}".format(code)
            if code in (502, 503, 429):
                time.sleep(RETRY_DELAY)
                continue
            raise
        except requests.RequestException as e:
            last = str(e)
            time.sleep(RETRY_DELAY)
    raise RuntimeError("请求失败（重试 {} 次）：{}".format(MAX_RETRY, last))


def unwrap(resp: dict) -> dict:
    """兼容 {checked_in:...} 与 {code:0, data:{checked_in:...}} 两种返回格式"""
    if "checked_in" not in resp and isinstance(resp.get("data"), dict) \
            and "checked_in" in resp["data"]:
        return resp["data"]
    return resp


def reward_desc(st: dict) -> str:
    """描述签到接口返回的“今日签到奖励”，不含账号总余额。
    注意：credits 是该接口返回的当日签到积分，并非账号总余额；
    真实总余额需在 Trae 客户端（头像→用量/积分）查看。"""
    base = st.get("credits")
    extra = st.get("extra_credits")
    parts = []
    if base is not None:
        parts.append("今日签到奖励积分：{}".format(base))
    if extra:
        parts.append("额外奖励：{}".format(extra))
    return "；".join(parts) if parts else ""


def checkin_api(name: str, auth: dict, device_id: str, only_status: bool,
                req_source: int):
    """执行签到，返回 (是否成功, 结果描述)；结果描述用于日志与通知，不泄露 token"""
    token = auth["token"]
    region = (auth.get("userRegion") or {}).get("region")
    try:
        status = unwrap(api(API_BASE + "/status", token, region, device_id, req_source))
    except Exception as e:
        detail = "查询签到状态失败：{}（登录态可能已过期，请打开 Trae CN 重新登录）".format(e)
        log("[{}] {}".format(name, detail))
        return False, detail

    if status.get("checked_in"):
        detail = "今日已签到（{}）".format(reward_desc(status))
        log("[{}] {}".format(name, detail))
        return True, detail
    if not status.get("enable"):
        detail = "签到当前不可用：{}".format(json.dumps(status, ensure_ascii=False)[:200])
        log("[{}] {}".format(name, detail))
        return False, detail
    if only_status:
        detail = "今日未签到（仅查询模式，未领取）"
        log("[{}] {}".format(name, detail))
        return True, detail

    try:
        claim = api(API_BASE + "/claim", token, region, device_id, req_source)
    except Exception as e:
        detail = "领取积分失败：{}".format(e)
        log("[{}] {}".format(name, detail))
        return False, detail

    if claim.get("code") == 0:
        try:
            after = unwrap(api(API_BASE + "/status", token, region, device_id, req_source))
            detail = "签到成功（{}）".format(reward_desc(after))
        except Exception:
            detail = "签到成功"
        log("[{}] {}".format(name, detail))
        return True, detail
    if claim.get("code") in (10001, 11001, 11002, 11003):  # 各类“今日已签到”
        detail = "今日已签到"
        log("[{}] {}".format(name, detail))
        return True, detail
    detail = "签到失败：{}".format(claim.get("message") or json.dumps(claim, ensure_ascii=False)[:200])
    log("[{}] {}".format(name, detail))
    return False, detail


# ---- 通知 ----
def build_summary(results) -> str:
    """组装多账号签到结果汇总文本（按账号区分）"""
    date = datetime.now().strftime("%Y-%m-%d")
    lines = ["Trae 每日签到结果（{}）".format(date), ""]
    if not results:
        lines.append("未找到任何可用登录态，请先打开 Trae CN 客户端登录。")
    else:
        for idx, (label, ok, detail) in enumerate(results, 1):
            mark = "[成功]" if ok else "[失败]"
            lines.append("{}. 账号「{}」{} {}".format(idx, label, mark, detail))
        ok_n = sum(1 for _, ok, _ in results if ok)
        lines.append("")
        lines.append("共 {} 个账号，成功 {} 个，失败 {} 个。".format(
            len(results), ok_n, len(results) - ok_n))
    return "\n".join(lines)


def notify_email(email_cfg: dict, subject: str, content: str) -> bool:
    """通过 SMTP 发送邮件（默认 465 SSL）。返回是否发送成功。"""
    host = email_cfg.get("smtp_host")
    port = email_cfg.get("smtp_port", 465)
    use_ssl = email_cfg.get("use_ssl", True)
    user = email_cfg.get("from")
    auth_code = email_cfg.get("auth_code")
    to_list = email_cfg.get("to") or []
    if not all([host, user, auth_code, to_list]):
        log("邮件通知：配置不完整（host/from/auth_code/to），已跳过")
        return False
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = "Trae自动签到 <{}>".format(user)
        msg["To"] = ", ".join(to_list)
        msg["Subject"] = Header(subject, "utf-8")
        if use_ssl:
            server = smtplib.SMTP_SSL(host, int(port), timeout=30)
        else:
            server = smtplib.SMTP(host, int(port), timeout=30)
            server.starttls()
        try:
            server.login(user, auth_code)
            server.sendmail(user, to_list, msg.as_string())
        finally:
            server.quit()
        log("邮件通知：已发送给 {}".format(", ".join(to_list)))
        return True
    except Exception as e:
        log("邮件通知：发送失败：{}".format(e))
        return False


def notify_wecom(wecom_cfg: dict, content: str) -> bool:
    """通过企业微信机器人 webhook 发送文本消息。返回是否发送成功。"""
    url = wecom_cfg.get("webhook_url")
    if not url:
        log("企微通知：webhook_url 未配置，已跳过")
        return False
    try:
        payload = {"msgtype": "text", "text": {"content": content}}
        r = requests.post(url, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get("errcode") != 0:
            log("企微通知：接口返回错误：{}".format(data.get("errmsg")))
            return False
        log("企微通知：已发送")
        return True
    except Exception as e:
        log("企微通知：发送失败：{}".format(e))
        return False


def send_notifications(notify_cfg: dict, results) -> None:
    """按配置开关发送通知；两个渠道互相独立，单个失败不影响另一个。"""
    if not (notify_cfg or {}).get("enabled"):
        log("通知：总开关未开启，跳过")
        return
    content = build_summary(results)
    subject = "Trae 每日签到结果 {}".format(datetime.now().strftime("%Y-%m-%d"))

    email_cfg = notify_cfg.get("email") or {}
    if email_cfg.get("enabled"):
        notify_email(email_cfg, subject, content)
    else:
        log("通知：邮件渠道未开启，跳过")

    wecom_cfg = notify_cfg.get("wecom") or {}
    if wecom_cfg.get("enabled"):
        notify_wecom(wecom_cfg, content)
    else:
        log("通知：企微渠道未开启，跳过")


# ---- 主流程 ----
def main():
    args = set(sys.argv[1:])
    only_status = "--status" in args
    no_notify = "--no-notify" in args

    cfg = load_config()
    ck = cfg_checkin(cfg)
    req_source = ck.get("req_source", 1)
    app_dirs = ck.get("app_dirs") or ["Trae CN"]
    extra_paths = ck.get("extra_paths") or []
    device_fallback = ck.get("device_id_fallback", "")

    log("=" * 56)
    log("Trae 每日自动签到开始（{}）".format("仅查询" if only_status else "签到并领取"))
    log("=" * 56)

    accounts = load_accounts(app_dirs, extra_paths)
    results = []  # [(label, ok, detail)]
    if not accounts:
        log("ERROR: 没有可用登录态，请先打开 Trae CN 客户端并登录一次")
    else:
        device_id = load_device_id(app_dirs, extra_paths, device_fallback)
        if not device_id:
            log("WARN: 未能自动识别设备 ID，将使用兜底值（可能影响签到）")
        for name, auth in accounts:
            label = account_label(auth, name)
            ok, detail = checkin_api(label, auth, device_id, only_status, req_source)
            results.append((label, ok, detail))

    # 通知：仅默认签到模式发送，--status 与 --no-notify 不发
    if not only_status and not no_notify:
        send_notifications(cfg_notify(cfg), results)

    return 0 if results and all(ok for _, ok, _ in results) else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception as e:
        log("未捕获异常：{}".format(e))
        code = 1
    try:
        if sys.stdin and sys.stdin.isatty():
            input("\n按回车键退出...")
    except Exception:
        pass
    sys.exit(code)
