# Trae CN 每日自动签到（领积分）

每天自动完成 Trae CN 的「每日签到」并领取积分，**无需打开客户端、不模拟点击**，
直接读取客户端本地登录态、调用官方签到接口完成。整包复制到其它装了 Trae CN 的
机器即可用。

## 原理
- 签到入口本就在客户端内部：点头像 → 签到。本脚本读取登录态后直连
  `api.trae.cn` 官方接口，等效于自动点签到。
- 只读登录态文件，不修改任何文件、不打印 token、不泄露敏感信息。

## 文件
| 文件 | 作用 |
|---|---|
| `checkin_trae.py` | 签到脚本本体（可跨机器） |
| `config.json` | **独立配置文件**：多账号目录、签到参数、邮件/企微通知与开关 |
| `install_checkin.cmd` | 一键安装：装依赖 + 注册每天 08:00 静默执行的计划任务 |
| `run_checkin.cmd` | 手动立即签到一次（前台可见输出，触发通知） |
| `uninstall_checkin.cmd` | 删除计划任务 |
| `checkin.log` | 签到结果日志（每天留痕） |

## 配置（config.json）
脚本的**所有可调项都集中在 `config.json`**，改完直接生效，无需动代码。

```jsonc
{
  "checkin": {
    "req_source": 1,                    // 签到来源：1=Trae CN IDE，2=SOLO/企业版
    "app_dirs": ["Trae CN"],            // 多账号：每个客户端目录列一个（都会扫描签到）
    "extra_paths": [],                  // 额外登录态路径（一般留空）
    "device_id_fallback": ""
  },
  "notify": {
    "enabled": true,                    // 通知总开关：false=所有通知都不发
    "email": {
      "enabled": true,                  // 邮件渠道开关
      "smtp_host": "smtp.163.com",
      "smtp_port": 465,
      "use_ssl": true,
      "from": "YOUR_SEND_EMAIL@163.com",    // 发件邮箱
      "auth_code": "YOUR_AUTH_CODE",      // SMTP 授权码
      "to": ["YOUR_RECEIVER_EMAIL@qq.com"]     // 收件人，可多个
    },
    "wecom": {
      "enabled": true,                  // 企微渠道开关
      "webhook_url": "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_WEBHOOK_KEY"
    }
  }
}
```

**开关方式**（两个渠道互相独立）：
- 只关邮件：`notify.email.enabled` 改为 `false`
- 只关企微：`notify.wecom.enabled` 改为 `false`
- 全关：`notify.enabled` 改为 `false`；或运行 `python checkin_trae.py --no-notify`

## 多账号
有多个 Trae 账号时：把每个客户端的应用数据目录名加进 `checkin.app_dirs`
（例如 `["Trae CN", "TRAE SOLO CN"]`），脚本会逐个扫描、按账号分别签到，
通知里按**用户名**区分（如「YOUR_USERNAME」），每个账号一行展示结果。
注意：账号要各自在该客户端登录过一次才会被识别。

## 通知内容
每次签到结束自动汇总所有账号结果，同时走已开启的渠道发送：
- **邮件**：163 SMTP（465 SSL）发送到 `notify.email.to`
- **企微**：机器人 webhook 发送文本消息

内容示例：
```
Trae 每日签到结果（2026-09-29）

1. 账号「YOUR_USERNAME1」[成功] 今日已签到（今日签到奖励积分：150；额外奖励：50）
2. 账号「YOUR_USERNAME2」[失败] 签到失败：登录态已过期

共 2 个账号，成功 1 个，失败 1 个。
```

## 快速开始
1. 用 Trae CN 客户端登录过一次（脚本依赖这个登录态）。
2. 按需改 `config.json`（通知开关、收件邮箱等）。
3. 双击 `install_checkin.cmd` 完成安装。
4. 之后每天 08:00 会自动静默签到，结果写 `checkin.log`，并按配置发邮件/企微通知。

需要立即验证时双击 `run_checkin.cmd`，看到
`签到成功`/`今日已签到` + `邮件通知：已发送`/`企微通知：已发送` 即正常。

## 复制到其它机器
- 把整个文件夹拷到新机器（任意位置，路径无中文更稳）。
- **注意**：只是复制文件夹并不会自动注册计划任务——Windows 计划任务是每台电脑
  各自注册的，所以**每台新机器都必须手动双击运行一次 `install_checkin.cmd`**，
  任务才会生成并开始每天 8:00 自动签到。
- 新机器满足：装有 Python 3.8+（脚本会自动找 Anaconda 或 PATH 里的 python），
  且 Trae CN 客户端已登录过。
- 双击 `install_checkin.cmd` 后，看到 `Done` 并提示任务已注册即成功。
  任务名统一为 `TraeCN_DailyCheckin`，重复安装会覆盖。
- 安装时的「[3/4] Verifying login state」只是**验证登录态**（只查询、不领取），
  显示 `今日未签到（仅查询模式，未领取）` 属正常；真正领取由 8:00 的定时任务完成。

## 依赖
- `requests`、`cryptography`（安装器会自动 `pip install`；若机器已有也可直接运行）。

## 说明与注意
- 日志里的「今日签到奖励积分」是签到接口返回的**当日签到奖励**（例如 TraeCode 每日签到 150、额外奖励 50），
  **不是你的账号总余额**。账号真实总余额请在 Trae 客户端点头像 → 用量/积分面板查看，
  该签到接口不提供余额，脚本无法读取。
- 任务按「仅当用户登录时运行」，即电脑开机且处于登录状态才会在 8:00 触发；
  若当天 8 点电脑关机/睡眠，当日会错过，可在开机后双击 `run_checkin.cmd` 补签。
- 登录态过期（如长时间未开客户端）时，日志会提示「登录态可能已过期」，
  打开一次 Trae CN 客户端刷新登录态即可，无需改动脚本。
- 每天重复执行不会重复领取，脚本会自动判断「今日已签到」并跳过。

## 手动运行（不注册定时任务）
只想要脚本、不要计划任务时：`python checkin_trae.py`（查询+领取+通知），
`python checkin_trae.py --status`（只查询今日状态，不领取不通知），
`python checkin_trae.py --no-notify`（签到但本次不通知）。

> 提示：`config.json` 里含邮箱授权码与企微 key，属于私密配置，请勿分享给他人。
> 脚本不会在日志里打印这些敏感值。
