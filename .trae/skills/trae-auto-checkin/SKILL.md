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

The server has no Trae client, so export login state from a signed-in machine.
No client-side Python is required — pick any of these:

```bash
# Python available:
pip install cryptography && python export_local.py
```

```powershell
# No Python (Windows ships PowerShell):
powershell -NoProfile -ExecutionPolicy Bypass -File export_local.ps1
```

Or skip tooling entirely: open `%APPDATA%\Trae CN\User\globalStorage\storage.json` in Notepad,
`Ctrl+A` / `Ctrl+C`, and paste the whole file. The server parses it and extracts the ciphertext
plus the real device fingerprint by itself.

In the panel, open the 账号 view, paste the JSON line (or the whole storage.json), optionally set a
remark name, then click 导入账号. The server decrypts it and stores the credential.
Treat `trae_export.json` as secret and delete it after importing.

Important for the OpenAI-compatible gateway: paste the **whole JSON entry** from
`trae_export.json` (it carries the client's real `machine_id` / `device_id` / `ide_version`),
not just the bare ciphertext. The upstream chat endpoint rate-limits requests coming from
random device fingerprints (returns 4011), so accounts without real fingerprints will fail
on `/v1/chat/completions`.

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

## 5. OpenAI-compatible gateway

The panel also exposes the accounts' chat capability as an OpenAI-compatible API
for Cursor / Cherry Studio / Cline / OpenAI SDK. Open 面板 →「API 接入」 and copy:

- Base URL: `http://<server-ip>:7864/v1`
- API Key: the gateway key set in that view (empty means no auth; intranet only)
- Model ID: pick from the listed models (from your account's available model list)

Endpoints: `GET /v1/models`, `POST /v1/chat/completions` (streaming and non-streaming).
Multiple accounts are tried in order, failing over to the next one.

```bash
curl http://<server-ip>:7864/v1/chat/completions \
  -H "Authorization: Bearer <gateway-key>" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-V3","messages":[{"role":"user","content":"hello"}]}'
```

Requires accounts imported with the client's real device fingerprint (see step 2), and the
gateway should not be exposed publicly without an API key.

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
