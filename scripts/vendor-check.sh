#!/usr/bin/env bash
# CI 里用：只有 requirements.txt 变了才重新下载 wheel（省时间、省流量）。
# 上游改依赖 → 本脚本自动把新 wheel 连同锁文件一起更新，随下一次提交进仓库，
# 于是 Dockerfile 那边永远能离线装齐。
set -euo pipefail
cd "$(dirname "$0")/.."

CUR=$(sha256sum requirements.txt | awk '{print $1}')
OLD=$(cat vendor/requirements.sha256 2>/dev/null || echo "")

if [ "$CUR" = "$OLD" ] && [ -f vendor/requirements.lock.txt ]; then
  echo "✅ requirements.txt 未变化，沿用已提交的 wheels（$(ls vendor/wheels | wc -l) 个）"
  exit 0
fi

echo "⚠️ requirements.txt 变了（$OLD -> $CUR），重新下载 wheels"
# CI runner 在境外，直连 PyPI 即可
bash scripts/vendor-sync.sh
echo "$CUR" > vendor/requirements.sha256
echo "✅ 已更新 vendor/"
