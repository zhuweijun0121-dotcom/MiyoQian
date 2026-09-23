#!/bin/sh
set -e

CONFIG_PATH="${MIYOUQIAN_CONFIG_PATH:-/app/state/config.yaml}"
CONFIG_DIR="$(dirname "$CONFIG_PATH")"

umask 077

# 创建必要的目录
mkdir -p "$CONFIG_DIR" "$CONFIG_DIR/data" "$CONFIG_DIR/logs"

# 如果 config.yaml 不存在，从示例配置复制
if [ ! -f "$CONFIG_PATH" ]; then
  echo "[entrypoint] config.yaml not found, copying from config.example.yaml"
  cp /app/config.example.yaml "$CONFIG_PATH"
fi

chmod 700 "$CONFIG_DIR" "$CONFIG_DIR/data" "$CONFIG_DIR/logs"
chmod 600 "$CONFIG_PATH"

exec "$@"
