#!/bin/sh
set -e

# 如果 config.yaml 不存在，从示例配置复制
if [ ! -f /app/config.yaml ]; then
  echo "[entrypoint] config.yaml not found, copying from config.example.yaml"
  cp /app/config.example.yaml /app/config.yaml
fi

# 创建必要的目录
mkdir -p /app/data /app/logs

exec "$@"
