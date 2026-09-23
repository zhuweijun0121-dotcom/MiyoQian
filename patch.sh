#!/bin/bash
# 在上游代码基础上打补丁。
#
# 设计原则：
#   1. 上游代码为准，本脚本只做最小必要改动，不做功能增强。
#   2. 幂等 —— 每次同步后重复执行结果一致，不会叠加。
#   3. 每个补丁独立校验，任一失败立即退出（避免推残缺代码）。
#
# 补丁清单：
#   A. config.py —— bbs 任务开关改用 setdefault，避免每次规范化配置时
#      把用户自定义的 read/like/share 强制重置为 False。
#      （上游 config.py:185-187 仍为硬编码 bbs["read"]=False 等）
#
# 已退役的补丁（上游已原生实现，不要再打）：
#   - notifier.py telegram 自定义 api_url —— 上游 PR #16 (2026-09-04) 已原生支持，
#     且实现更规范（api_url 作为 base URL），配置项与 Web UI 均已提供。

set -u

fail() { echo "❌ $1"; exit 1; }

# ---------------------------------------------------------------- A
F="miyouqian/core/config.py"
python3 - "$F" <<'PYEOF'
import re, sys
p = sys.argv[1]
s = open(p, encoding="utf-8").read()
old = '''    bbs["read"] = False
    bbs["like"] = False
    bbs["share"] = False
'''
new = '''    bbs.setdefault("read", False)
    bbs.setdefault("like", False)
    bbs.setdefault("share", False)
'''
if old in s:
    s = s.replace(old, new, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("   (已替换为 setdefault)")
elif new in s:
    print("   (已是 setdefault，跳过)")
else:
    print("❌ config.py 补丁未生效：未找到目标代码，上游可能已改动此处")
    sys.exit(1)
PYEOF
[ $? -eq 0 ] || fail "config.py 补丁失败"
grep -q 'bbs.setdefault("read", False)' "$F" || fail "config.py 补丁未生效"
echo "✅ A config.py bbs 开关保留用户设置"

echo "🎉 全部补丁已应用"
