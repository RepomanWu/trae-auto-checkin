#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy / CodeBuddy 登录凭据导出工具（设备授权登录流程）。

为什么需要它：WorkBuddy 桌面端 5.6+ 的本地登录态是**加密的**（解密密钥来自它自己的
Electron 二进制，服务器上拿不到），所以要在**本机**通过官方「设备授权」流程换取明文凭据，
再把输出的 JSON 粘贴进面板「账号」页。

用法：
    pip install requests
    python export_workbuddy.py                 # 国内版
    python export_workbuddy.py --realm global  # 国际版
    python export_workbuddy.py --url-only      # 只打印授权链接（不等待、不轮询）

流程（与官方 CLI 登录一致）：
    1) POST {base}/v2/plugin/auth/state?platform=CLI  → 拿 state + authUrl
    2) 浏览器打开 authUrl 完成登录
    3) GET  {base}/v2/plugin/auth/token?state=…        → accessToken / refreshToken
    4) GET  {base}/v2/plugin/login/account?state=…     → uid / nickname

输出的 JSON 直接粘到面板「账号」页（平台选 WorkBuddy / CodeBuddy）即可导入。
"""

from __future__ import annotations

import argparse
import json
import sys
import time

import requests

CN_BASE, CN_ORIGIN = "https://copilot.tencent.com", "https://www.codebuddy.cn"
GL_BASE, GL_ORIGIN = "https://www.workbuddy.ai", "https://www.workbuddy.ai"
CLIENT_UA = "CLI/2.63.2 CodeBuddy/2.63.2"
POLL_INTERVAL = 2
POLL_TRIES = 60


def _headers(origin: str, token: str = "") -> dict:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": origin,
        "Referer": origin + "/",
        "User-Agent": CLIENT_UA,
    }
    if token:
        h["Authorization"] = "Bearer " + token
    return h


def _call(method: str, url: str, origin: str, body=None, token: str = "", timeout: int = 30):
    """发请求并解信封 {code,msg,data}；code != 0 抛 RuntimeError（含业务文案）。"""
    r = requests.request(method, url, headers=_headers(origin, token),
                         json=body, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError("上游 HTTP {}：{}".format(r.status_code, (r.text or "")[:200]))
    try:
        env = r.json() if r.content else {}
    except ValueError:
        raise RuntimeError("上游返回非 JSON：{}".format((r.text or "")[:200]))
    code = env.get("code")
    if code not in (0, None):
        raise RuntimeError("code={} msg={}".format(code, env.get("msg") or ""))
    return env.get("data") or {}


def main() -> int:
    ap = argparse.ArgumentParser(description="WorkBuddy / CodeBuddy 登录凭据导出（设备授权登录）")
    ap.add_argument("--realm", choices=["cn", "global"], default="cn",
                    help="cn=国内版（默认），global=国际版")
    ap.add_argument("--url-only", action="store_true", help="只打印授权链接后退出")
    ap.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = ap.parse_args()

    base, origin = (GL_BASE, GL_ORIGIN) if args.realm == "global" else (CN_BASE, CN_ORIGIN)
    print("[*] 版本：{}（{}）".format("国际版 global" if args.realm == "global" else "国内版 cn", base))

    try:
        st = _call("POST", base + "/v2/plugin/auth/state?platform=CLI", origin, body={})
    except Exception as e:
        print("[x] 获取授权链接失败：{}".format(e), file=sys.stderr)
        print("    （上游接口可能已变更，或网络不通）", file=sys.stderr)
        return 1
    state = st.get("state") or ""
    auth_url = st.get("authUrl") or ""
    if not state or not auth_url:
        print("[x] 上游未返回 state / authUrl，接口可能已变更", file=sys.stderr)
        return 1

    print("\n请在浏览器打开下面的链接完成登录：\n\n{}\n".format(auth_url))
    if args.url_only:
        return 0
    if not args.no_open:
        try:
            import webbrowser
            webbrowser.open(auth_url)
        except Exception:
            pass

    try:
        input("完成登录后按回车继续…")
    except EOFError:
        pass

    tok = {}
    for i in range(POLL_TRIES):
        try:
            data = _call("GET", base + "/v2/plugin/auth/token?state=" + state, origin)
            if data.get("accessToken"):
                tok = data
                break
        except RuntimeError as e:
            # 登录未完成时上游返回业务错误（如 "login ing"），继续轮询
            if i == 0:
                print("   等待登录完成…（{}）".format(e))
        time.sleep(POLL_INTERVAL)
        if (i + 1) % 5 == 0:
            print("   仍在等待…（{}s）".format((i + 1) * POLL_INTERVAL))

    if not tok.get("accessToken"):
        print("\n[x] 未取到 accessToken：请确认已在该浏览器里登录成功", file=sys.stderr)
        return 1

    acct = {}
    try:
        acct = _call("GET", base + "/v2/plugin/login/account?state=" + state, origin,
                     token=tok["accessToken"])
    except Exception:
        pass

    domain = tok.get("domain") or ""
    realm = args.realm
    if not args.realm or args.realm == "cn":
        if domain.lower().endswith("workbuddy.ai"):
            realm = "global"

    out = {
        "access_token": tok.get("accessToken") or "",
        "refresh_token": tok.get("refreshToken") or "",
        "expires_in": tok.get("expiresIn") or 0,
        "domain": domain,
        "realm": realm,
        "uid": acct.get("uid") or "",
        "enterprise_id": acct.get("enterpriseId") or "",
        "nickname": acct.get("nickname") or "",
    }
    print("\n=== 复制下面这一整行 JSON，粘到面板「账号」页（平台选 WorkBuddy / CodeBuddy）===\n")
    print(json.dumps(out, ensure_ascii=False))
    print("\n（这串凭据等同账号权限，请勿提交到仓库或发给他人）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
