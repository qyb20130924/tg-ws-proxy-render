#!/bin/bash
# Telegram WebSocket Proxy 启动脚本
# 适用于 Render 部署环境

set -e

echo "Starting Telegram WebSocket Proxy..."

# 检查 Python 是否可用
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 not found"
    exit 1
fi

# 安装依赖
echo "Installing dependencies..."
pip install --no-cache-dir -r requirements.txt

# 启动服务
echo "Starting proxy service on port ${PORT:-10000}..."
exec python3 proxy/tg_ws_proxy.py
