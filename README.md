# Trae 多账号管理面板

自托管的 **Trae CN 多账号管理面板**：统一管理多个账号，自动每日签到、定时刷新用量、
监控积分消耗、留存历史与日志，支持邮件 / 企业微信通知，**Docker 一键部署到服务器**。

架构参考了 [workbuddy2api-panel](https://github.com/linguo2625469/workbuddy2api-panel)：
Web 管理面板 + 多账号池 + 定时任务 + 用量视图 + 单命令部署。本项目聚焦 Trae CN，
不含对外 API 网关。

## 功能

| 能力 | 说明 |
|---|---|
| 🔑 **多账号管理** | 面板导入 / 删除账号；导入即解密并保存凭证，自动用 refreshToken 续期 |
| ⏰ **自动签到** | 每日定时（可配多个时刻）对全部账号签到，幂等安全，可手动一键触发 |
| 🧭 **任务中心** | 按账号 × 任务展示完成状态并支持一键执行（当前内置「每日签到」「用量快照」） |
| 📊 **用量监控** | 查询积分总量 / 已消耗 / 剩余 / 积分包到期，按天留存历史并出趋势图 |
| 🔔 **通知** | 邮件（SMTP）+ 企业微信机器人，开关独立，支持测试发送 |
| 🖥️ **Web 面板** | 概览 / 账号 / 用量 / 任务 / 日志 / 设置六个视图，访问密钥保护 |
| 🐳 **一键部署** | `docker-compose` 单命令拉起，`.env` 填 key 即可 |

## 原理

不打开客户端、不模拟点击：脚本直接解密本机客户端保存的登录态（`iCubeAuthInfo://icube.cloudide`，
tc 加密格式），提取 accessToken / refreshToken，然后调用官方接口：

- 签到状态：`POST api.trae.cn/trae/api/v2/ug/checkin_credits/status`
- 领取积分：`POST api.trae.cn/trae/api/v2/ug/checkin_credits/claim`
- 用量查询：`POST api.trae.cn/trae/api/v2/pay/ide_user_ent_usage`
- 令牌续期：`POST api.trae.cn/cloudide/api/v3/trae/oauth/ExchangeToken`

> 上述接口与加解密为逆向自官方客户端的非公开实现，可能随客户端版本变化。仅限管理**你自己的账号**。

## 快速开始（服务器 Docker 一键部署）

在服务器上：

```bash
git clone <你的仓库地址> trae-checkin && cd trae-checkin   # 或直接把文件夹上传到服务器
./deploy.sh
```

`deploy.sh` 会自动生成 `.env`（含随机面板密钥）并 `docker compose up -d --build`。
完成后输出面板地址，浏览器打开 `http://<服务器IP>:7860`，输入 `.env` 里的 `ADMIN_KEY` 即可。

手动方式：

```bash
cp .env.example .env    # 填写 ADMIN_KEY（面板密钥 = key）与 PORT
docker compose up -d --build
```

> 打不开时确认服务器安全组 / 防火墙已放行对应端口。

## 添加账号（关键步骤）

服务器上没有 Trae 客户端，需要从**你本机**导出登录态密文：

1. 在本机（已登录 Trae 客户端的机器）运行：

   ```bash
   pip install cryptography
   python export_local.py
   ```

   它会扫描 `Trae CN` / `TRAE SOLO CN` / `Trae` 等目录，输出 `trae_export.json`，
   并把每个账号的**密文**打印到终端。

2. 打开面板 → 「账号」→ 粘贴密文 →（可选）填备注名 → 「导入账号」。

   服务端解密后保存凭证。密文只在你的本机与你的服务器之间流转，不经过第三方。

## 配置（key 与来源）

面板「设置」视图可在线修改，保存后**热生效**（无需重启）：

- **面板访问密钥（key）**：保护面板与接口；留空表示不校验（仅建议内网/本机）。
- **签到来源（模型/来源口径）**：`1 = Trae CN（IDE / Work）`，`2 = SOLO / 企业版`。
- **签到时间 / 用量刷新间隔 / 时区 / 历史保留天数**。
- **通知**：邮件 SMTP、企业微信 webhook，含「发送测试通知」。

也可用环境变量注入（适合 Docker）：`TRAE_PANEL_ADMIN_KEY`、`TRAE_PANEL_PORT`、`TRAE_PANEL_HOST`、`TZ`。

## 任务中心说明（重要）

Trae CN 官方目前**可稳定调用**的自动化接口只有「每日签到」与「用量查询」；
官方「成长任务」类接口**未公开**，社区也无可验证的逆向成果，因此本项目**不臆造接口**，
只内置上述两个已验证任务。

扩展方式：在 `trae_panel/service.py` 的 `TASKS` 里加一条任务元数据，
并实现对应的处理函数（放到 `run_task` 的分支），面板会自动出现该任务。

## 目录结构

```
trae_panel/          服务端（FastAPI）
  config.py          配置加载/保存（data/config.json + 环境变量）
  crypto.py          tc 登录态解密、令牌刷新、出站指纹头
  api.py             Trae 接口调用（含 401 自动刷新、瞬时错误重试）
  store.py           账号与历史落盘（原子写）
  service.py         业务编排：导入、签到、任务、用量快照、通知
  notify.py          邮件 / 企业微信
  log.py             面板运行日志
  web.py             FastAPI 路由 + 定时调度
  __main__.py        入口：python -m trae_panel
panel/index.html     内嵌 Web 面板（无构建依赖）
export_local.py      本机登录态导出工具
Dockerfile / docker-compose.yml / deploy.sh / .env.example
```

## 本地直接运行（不走 Docker）

```bash
pip install -r requirements.txt
python -m trae_panel          # 监听 0.0.0.0:7860
```

本机想保留原有的 Windows 定时签到脚本，见文末「本机脚本」。

## 数据与安全

- 账号凭证（含明文令牌）保存在 `data/accounts.json`，仅供服务端调用官方接口。
  **请勿把 `data/` 提交到公开仓库**；Docker 部署时它只挂载在你自己的卷里。
- 面板接口需要 `X-Admin-Key`，请务必设置 `.env` 的 `ADMIN_KEY`。
- 出站请求带固定的设备/机器指纹（导入时生成并持久化），保持稳定、避免指纹漂移。
- 日志与面板响应都不会回显令牌。

## 常见问题

- **签到返回「登录态已失效」**：refreshToken 也过期了，去本机客户端重新登录一次，再导出密文重新导入。
- **签到不可用 / enable=false**：账号当日未满足条件或活动调整，面板日志会给出原始返回。
- **积分数字**：面板展示的是官方用量接口返回的总量 / 已消耗 / 剩余；签到奖励是当日签到所得，二者含义不同。

## 本机脚本（原有，可选）

仓库仍保留本机 Windows 版脚本，适合"就在装了客户端的机器上定时签到"：

| 文件 | 作用 |
|---|---|
| `checkin_trae.py` | 本机签到脚本（读取本机登录态，直连官方接口，可多账号） |
| `config.example.json` | 配置模板：复制为 `config.json` 后填写（含通知邮箱 / 授权码，已被 `.gitignore` 排除） |
| `install_checkin.cmd` / `run_checkin.cmd` / `uninstall_checkin.cmd` | 安装 / 立即执行 / 卸载计划任务 |

> 服务器面板与它二选一即可，互不冲突。
