# -*- coding: utf-8 -*-
"""通知渠道：邮件（SMTP）+ 企业微信机器人 webhook，两者互相独立。"""

from __future__ import annotations

import smtplib
from datetime import datetime
from email.header import Header
from email.mime.text import MIMEText

import requests

from .log import log


def build_summary(results) -> str:
    """组装多账号结果汇总文本。results: [(label, ok, detail)]"""
    date = datetime.now().strftime("%Y-%m-%d")
    lines = ["Trae 面板任务结果（{}）".format(date), ""]
    if not results:
        lines.append("没有账号被处理。")
    else:
        for idx, (label, ok, detail) in enumerate(results, 1):
            mark = "[成功]" if ok else "[失败]"
            lines.append("{}. 账号「{}」{} {}".format(idx, label, mark, detail))
        ok_n = sum(1 for _, ok, _ in results if ok)
        lines.append("")
        lines.append("共 {} 个账号，成功 {} 个，失败 {} 个。".format(
            len(results), ok_n, len(results) - ok_n))
    return "\n".join(lines)


def send_email(cfg: dict, subject: str, content: str) -> bool:
    host = cfg.get("smtp_host")
    port = cfg.get("smtp_port", 465)
    use_ssl = cfg.get("use_ssl", True)
    user = cfg.get("from")
    auth_code = cfg.get("auth_code")
    to_list = cfg.get("to") or []
    if not all([host, user, auth_code, to_list]):
        log("邮件通知：配置不完整（host/from/auth_code/to），已跳过")
        return False
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = "Trae面板 <{}>".format(user)
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


def send_wecom(cfg: dict, content: str) -> bool:
    url = cfg.get("webhook_url")
    if not url:
        log("企微通知：webhook_url 未配置，已跳过")
        return False
    try:
        r = requests.post(url, json={"msgtype": "text", "text": {"content": content}}, timeout=30)
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


def send_notifications(notify_cfg: dict, results, title: str = "Trae 每日签到结果") -> None:
    if not (notify_cfg or {}).get("enabled"):
        log("通知：总开关未开启，跳过")
        return
    content = build_summary(results)
    subject = "{} {}".format(title, datetime.now().strftime("%Y-%m-%d"))
    email_cfg = notify_cfg.get("email") or {}
    if email_cfg.get("enabled"):
        send_email(email_cfg, subject, content)
    wecom_cfg = notify_cfg.get("wecom") or {}
    if wecom_cfg.get("enabled"):
        send_wecom(wecom_cfg, content)
