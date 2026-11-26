#!/usr/bin/env bash
# 自检：向 gt-solver 的自定义接口发一道【真实的米游社验证码】，看能否解出 validate。
#
# 用法：
#   bash scripts/test-gt-solver.sh                          # 默认 http://127.0.0.1:10721/custom
#   GT_SOLVER_URL=http://192.168.1.5:10721/custom bash scripts/test-gt-solver.sh
#
# 做的事：
#   1. 调米游社 misc/api/createVerification 拿新鲜 gt + challenge（不需要登录）；
#   2. 按自定义接口规范请求 gt-solver；
#   3. 打印返回体并判断协议是否合规、是否解出。
# 需要本机有 python3 + 仓库依赖（在容器里跑也行）。
set -euo pipefail
cd "$(dirname "$0")/.."
exec python3 scripts/test-gt-solver.py
