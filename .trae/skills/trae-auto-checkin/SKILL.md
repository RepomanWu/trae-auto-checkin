---
name: trae-auto-checkin
description: Deploy the Trae multi-account check-in panel via Docker and import Trae accounts. Use when the user asks to deploy trae-auto-checkin, set up a Trae auto check-in panel, or monitor Trae credits. Do not use for unrelated Trae coding tasks.
---

# Trae Auto Check-in Panel

Deploy this repository's self-hosted panel: multi-account Trae CN check-in, task center,
credit/usage monitoring, notifications, all behind a key-protected web UI.

## Prerequisites

- Target server: Docker + Docker Compose.
- A machine where the Trae client is signed in (needed to export login state).
- On that exporting machine: Python 3.8+.

## 1. Deploy

```bash
git clone https://github.com/RepomanWu/trae-auto-checkin.git
cd trae-auto-checkin
./deploy.sh
```

If it prints `Permission denied`, run `bash deploy.sh` instead.

`deploy.sh` generates `.env` with a random `ADMIN_KEY`, then runs
`docker compose up -d --build` and prints the panel URL and key.

Manual alternative:

```bash
cp .env.example .env      # set ADMIN_KEY and PORT
docker compose up -d --build
```

Open `http://<server-ip>:<PORT>` and enter the `ADMIN_KEY`. If it will not open,
confirm the server firewall / security group allows that port.

Local run without Docker (from the repo root):

```bash
pip install -r requirements.txt
python -m trae_panel
```

## 2. Import accounts

The server has no Trae client, so export login state from a signed-in machine:

```bash
pip install cryptography
python export_local.py        # writes trae_export.json and prints each ciphertext
```

In the panel, open the 账号 view, paste the printed ciphertext, optionally set a
remark name, then click 导入账号. The server decrypts it and stores the credential.
Treat `trae_export.json` as secret and delete it after importing.

## 3. Configure key and source

Panel → 设置 (saved values take effect immediately, no restart):

- 面板访问密钥 — the panel key; empty means no auth check (local/intranet only).
- 签到来源 — `1` for Trae CN IDE/Work, `2` for SOLO/enterprise.
- 签到时间 — daily check-in times as `HH:MM`, comma separated.
- 用量刷新间隔 / 时区 / 历史保留天数.
- 通知 — email SMTP and WeCom webhook, with a test button.

## 4. Verify

- 概览 shows account count and today's checked-in count; click 全部签到 to run once.
- 用量 shows per-account totals and a daily trend chart.
- 运行日志 shows task/usage/system logs.

## Troubleshooting

| Symptom | Fix |
|---|---|
| 登录态已失效 / refresh failed | Re-sign-in in the Trae client, run `export_local.py` again, re-import |
| 签到 enable=false | Account not eligible or activity changed; read the raw response in 运行日志 |
| Panel unreachable | Check `docker compose logs -f`, the container is listening on 7864 inside; verify the mapped port and firewall |
| Build stuck pulling `python:3.11-slim` on a China server | Network speed, not a hang. Configure a Docker registry mirror, or set `PYTHON_BASE` and `PIP_INDEX_URL` in `.env`; see README 国内服务器加速 |
| Credits look low | Credits are the daily check-in reward; the account balance comes from the usage endpoint, shown in 用量 |

## Safety

- Use only accounts you own.
- `data/accounts.json` holds plaintext tokens; it is git-ignored. Never commit or share `data/`, `.env`, or `trae_export.json`.
- The endpoints and decryption are reverse-engineered from the official client and may change; they only cover verified features (daily check-in and usage query). Other endpoints are not implemented.
