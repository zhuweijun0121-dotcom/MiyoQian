#!/usr/bin/env bash
# 把 requirements.txt 解析出的依赖全部下载成 wheel，随仓库一起提交。
#
# 目的：Dockerfile 用 `pip install --no-index --find-links=vendor/wheels` 安装，
# 构建过程完全不访问 PyPI，也不再依赖 ghcr.io 的 uv 镜像。
# 于是「拉本仓源码 + 拉 Docker Hub 的基础镜像」就能构建，
# 不需要外网、不需要代理，也不会出现「开着代理反而访问不了国内源」的问题。
#
# 用法：
#   bash scripts/vendor-sync.sh                     # 走当前网络（或已设好的代理）
#   PIP_PROXY=http://192.168.1.5:7890 bash scripts/vendor-sync.sh
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/vendor-sync.sh
set -euo pipefail
cd "$(dirname "$0")/.."

PY_VER="3.11"
OUT="vendor/wheels"
LOCK="vendor/requirements.lock.txt"
# bookworm 的 glibc 是 2.36，2_28 与 2014(2_17) 的 wheel 都能跑；
# 只有 x86_64 的 wheel，因为镜像按 linux/amd64 构建。
PLATFORM_ARGS=(--platform manylinux_2_28_x86_64 --platform manylinux2014_x86_64)

EXTRA=()
[ -n "${PIP_INDEX_URL:-}" ] && EXTRA+=(-i "$PIP_INDEX_URL")
[ -n "${PIP_PROXY:-}" ] && EXTRA+=(--proxy "$PIP_PROXY")

rm -rf "$OUT"
mkdir -p "$OUT"

python3 -m pip download -d "$OUT" -r requirements.txt \
  --only-binary=:all: "${PLATFORM_ARGS[@]}" \
  --python-version "$PY_VER" --implementation cp --abi cp311 \
  "${EXTRA[@]}"

python3 - "$OUT" "$LOCK" <<'PY'
import pathlib, sys
out, lock = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
rows = []
for w in sorted(out.glob("*.whl")):
    parts = w.name.split("-")
    rows.append(f"{parts[0]}=={parts[1]}")
rows.sort(key=str.lower)
lock.write_text(
    "# 由 scripts/vendor-sync.sh 自动生成，请勿手改。\n"
    "# 目标：CPython 3.11 / linux amd64（bookworm）\n" + "\n".join(rows) + "\n",
    encoding="utf-8",
)
print(f"锁文件共 {len(rows)} 个依赖：")
print("\n".join("  " + r for r in rows))
PY

echo "✅ 已更新 $OUT（$(ls "$OUT" | wc -l) 个 wheel）与 $LOCK"
