#!/bin/bash
# 上游同步之后执行：把本仓的构建相关文件铺回去，并对上游源码做必要补丁校验。
#
# 设计原则：
#   1. miyouqian/ 下的业务源码保持与上游完全一致 —— 不打业务补丁。
#   2. 只改「构建/部署」这一层：docker/Dockerfile、docker/entrypoint.sh。
#      这两个文件是上游也会改的，所以真正的版本放在 mirror/ 下，
#      每次同步后用本脚本覆盖回去，保证可重复、不被 -X theirs 吞掉。
#   3. 幂等：重复执行结果一致。
#   4. 任一校验失败立即退出，避免推送残缺代码。
#
# 已退役的业务补丁（上游已原生实现，不要再打）：
#   - config.py bbs read/like/share —— 上游 2026-11-04 6de8376 改成
#       for key in ("read", "like", "share"): bbs[key] = parse_bool(bbs.get(key, False))
#     用户开关不再被重置。旧的 setdefault 补丁因为找不到锚点，让同步流水线
#     从 2026-10-29 起连续失败 69 次，已删除。
#   - notifier.py telegram 自定义 api_url —— 上游 PR #16 (2026-09-04) 已原生支持。

set -u

fail() { echo "❌ $1"; exit 1; }

echo "=== 1) 铺回本仓的 Dockerfile ==="
cp mirror/Dockerfile docker/Dockerfile
chmod 644 docker/Dockerfile
cmp -s mirror/Dockerfile docker/Dockerfile || fail "docker/Dockerfile 与 mirror/Dockerfile 不一致"
grep -q 'pip install --no-index --find-links=/tmp/wheels' docker/Dockerfile \
  || fail "docker/Dockerfile 缺少离线安装依赖步骤"
grep -q 'COPY mirror/config.default.yaml /app/config.default.yaml' docker/Dockerfile \
  || fail "docker/Dockerfile 未包含默认配置"
echo "✅ docker/Dockerfile 已更新（离线依赖 + 默认配置）"

echo "=== 2) 铺回本仓的 entrypoint.sh ==="
cp mirror/entrypoint.sh docker/entrypoint.sh
chmod 755 docker/entrypoint.sh
cmp -s mirror/entrypoint.sh docker/entrypoint.sh || fail "docker/entrypoint.sh 与 mirror/entrypoint.sh 不一致"
grep -q 'config.default.yaml' docker/entrypoint.sh \
  || fail "docker/entrypoint.sh 未优先使用 config.default.yaml"
echo "✅ docker/entrypoint.sh 已更新（首次启动用本仓默认配置）"

echo "=== 3) 校验默认配置可用 ==="
python3 - <<'PY' || fail "mirror/config.default.yaml 不是合法 YAML"
import sys, yaml
d = yaml.safe_load(open("mirror/config.default.yaml", encoding="utf-8"))
chans = (d.get("captcha") or {}).get("channels") or []
custom = [c for c in chans if c.get("provider") == "custom" and c.get("enable")]
assert custom, "默认配置里没有启用 custom 验证码渠道"
assert custom[0].get("url"), "custom 渠道缺少 url"
print(f"   验证码默认渠道: custom -> {custom[0]['url']}")
PY
echo "✅ 默认配置校验通过"

echo "🎉 全部构建改动已应用"
