#!/usr/bin/env bash
# 一键部署：在服务器上构建并启动 Trae 面板（Docker）
set -euo pipefail
cd "$(dirname "$0")"

if ! command -v docker >/dev/null 2>&1; then
  echo "[错误] 未检测到 docker，请先安装 Docker：https://docs.docker.com/engine/install/"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  DC="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  DC="docker-compose"
else
  echo "[错误] 未检测到 docker compose 插件"
  exit 1
fi

if [ ! -f .env ]; then
  KEY="${ADMIN_KEY:-$(head -c 32 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 24)}"
  PORT_VAL="${PORT:-7860}"
  cat > .env <<EOF
ADMIN_KEY=$KEY
PORT=$PORT_VAL
TZ=${TZ:-Asia/Shanghai}

# 国内服务器加速（可选，见 README「国内服务器加速」）
# PYTHON_BASE=docker.m.daocloud.io/library/python:3.11-slim
# PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
EOF
  echo "==> 已生成 .env"
  echo "    面板访问密钥(key): $KEY"
else
  # shellcheck disable=SC1091
  PORT_VAL="$(grep -E '^PORT=' .env | cut -d= -f2 || true)"
  PORT_VAL="${PORT_VAL:-7860}"
  echo "==> 检测到已有 .env，沿用现有配置"
fi

echo "==> 构建并启动（首次构建约需一两分钟）..."
$DC up -d --build

echo
echo "=========================================================="
echo " 部署完成"
echo " 面板地址 : http://<服务器IP>:${PORT_VAL}"
echo " 查看日志 : $DC logs -f"
echo " 停止服务 : $DC down"
echo "=========================================================="
echo "提示：如打不开，请确认服务器安全组/防火墙已放行 ${PORT_VAL} 端口。"
echo "提示：国内服务器首次构建若长时间卡在拉取镜像，见 README「国内服务器加速」。"
