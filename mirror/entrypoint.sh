#!/bin/sh
# 本仓版本：相对上游 docker/entrypoint.sh 只多一条 —— 首次启动时优先用
# /app/config.default.yaml（本仓预置：验证码走自建 gt-solver），没有才退回上游示例。
set -e

CONFIG_PATH="${MIYOUQIAN_CONFIG_PATH:-/app/state/config.yaml}"
CONFIG_DIR="$(dirname "$CONFIG_PATH")"

umask 077

# 创建必要的目录
mkdir -p "$CONFIG_DIR" "$CONFIG_DIR/data" "$CONFIG_DIR/logs"

# 如果 config.yaml 不存在，优先复制本仓预置的默认配置，其次退回上游示例
if [ ! -f "$CONFIG_PATH" ]; then
  if [ -f /app/config.default.yaml ]; then
    echo "[entrypoint] config.yaml not found, copying from config.default.yaml (mirror preset)"
    cp /app/config.default.yaml "$CONFIG_PATH"
  else
    echo "[entrypoint] config.yaml not found, copying from config.example.yaml"
    cp /app/config.example.yaml "$CONFIG_PATH"
  fi
fi

chmod 700 "$CONFIG_DIR" "$CONFIG_DIR/data" "$CONFIG_DIR/logs"
chmod 600 "$CONFIG_PATH"

exec "$@"
