#!/bin/bash
# 上游同步之后执行：把本仓的改造铺回去，并清掉上游带过来但本仓用不上的东西。
#
# 设计原则：
#   1. miyouqian/ 下的业务源码与上游**完全一致** —— 不打业务补丁。
#   2. 只改「构建 / 部署 / CI」这一层。这些文件上游也会改，所以真正的版本
#      放在 mirror/ 下，每次同步后由本脚本强制铺回，否则 `git merge -X theirs`
#      会把它们覆盖掉、或把手删的文件重新加回来。
#   3. 幂等：重复执行结果一致。
#   4. 任一校验失败立即退出；上游动了关键部分时主动报警，而不是静默构建出坏镜像。
#
# 已退役的业务补丁（上游已原生实现，不要再打）：
#   - config.py bbs read/like/share —— 上游 2026-11-04 6de8376 改成
#       for key in ("read", "like", "share"): bbs[key] = parse_bool(bbs.get(key, False))
#     用户开关不再被重置。旧的 setdefault 补丁因为找不到锚点，让同步流水线
#     从 2026-10-29 起连续失败 69 次，已删除。
#   - notifier.py telegram 自定义 api_url —— 上游 PR #16 (2026-09-04) 已原生支持。

set -u

fail() { echo "❌ $1"; exit 1; }
note() { echo "$1" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"; }

# ---------------------------------------------------------------- 0
# 上游 Dockerfile 变更哨兵
# 上游也会改 docker/Dockerfile，而我们每次都用 mirror/ 版本覆盖它 ——
# 万一上游改了 CMD / ENTRYPOINT（比如换了启动参数、改成别的工作目录），
# 我们的版本就会落后，构建出来的镜像可能根本起不来。所以关键指令不一致时
# 直接失败，逼人看一眼 mirror/Dockerfile，而不是静默产出坏镜像。
echo "=== 0) 检查上游 Dockerfile 是否变了 ==="
if [ -f docker/Dockerfile ] && [ -f mirror/Dockerfile ]; then
  upstream_cmd=$(grep -E '^[[:space:]]*(CMD|ENTRYPOINT)' docker/Dockerfile | sed 's/[[:space:]]\+/ /g')
  mirror_cmd=$(grep -E '^[[:space:]]*(CMD|ENTRYPOINT)' mirror/Dockerfile | sed 's/[[:space:]]\+/ /g')
  if [ "$upstream_cmd" != "$mirror_cmd" ]; then
    echo "上游 Dockerfile 的启动指令与本仓 mirror/Dockerfile 不一致："
    echo "--- 上游 ---"; echo "$upstream_cmd"
    echo "--- 本仓 ---"; echo "$mirror_cmd"
    note "### ⚠️ 上游 Dockerfile 启动指令变了，需要人工同步 mirror/Dockerfile"
    note '```'
    diff <(echo "$upstream_cmd") <(echo "$mirror_cmd") >> "${GITHUB_STEP_SUMMARY:-/dev/null}" 2>&1 || true
    note '```'
    fail "上游 Dockerfile 的 CMD/ENTRYPOINT 已变更，请更新 mirror/Dockerfile 后重跑"
  fi
  if ! diff -q docker/Dockerfile mirror/Dockerfile >/dev/null; then
    echo "ℹ️ 上游 Dockerfile 有非启动指令的差异（将被本仓版本覆盖）："
    diff -u docker/Dockerfile mirror/Dockerfile | head -40 || true
    note "### ℹ️ 上游 Dockerfile 有次要差异，已用本仓版本覆盖（详见 Actions 日志）"
  fi
fi
echo "✅ Dockerfile 启动指令一致"

# ---------------------------------------------------------------- 1
echo "=== 1) 铺回本仓的 Dockerfile ==="
cp mirror/Dockerfile docker/Dockerfile
chmod 644 docker/Dockerfile
cmp -s mirror/Dockerfile docker/Dockerfile || fail "docker/Dockerfile 与 mirror/Dockerfile 不一致"
grep -q 'pip install --no-index --find-links=/tmp/wheels' docker/Dockerfile \
  || fail "docker/Dockerfile 缺少离线安装依赖步骤"
grep -q 'COPY mirror/config.default.yaml /app/config.default.yaml' docker/Dockerfile \
  || fail "docker/Dockerfile 未包含默认配置"
echo "✅ docker/Dockerfile 已更新（离线依赖 + 默认配置）"

# ---------------------------------------------------------------- 2
echo "=== 2) 铺回本仓的 entrypoint.sh ==="
cp mirror/entrypoint.sh docker/entrypoint.sh
chmod 755 docker/entrypoint.sh
cmp -s mirror/entrypoint.sh docker/entrypoint.sh || fail "docker/entrypoint.sh 与 mirror/entrypoint.sh 不一致"
grep -q 'config.default.yaml' docker/entrypoint.sh \
  || fail "docker/entrypoint.sh 未优先使用 config.default.yaml"
echo "✅ docker/entrypoint.sh 已更新（首次启动用本仓默认配置）"

# ---------------------------------------------------------------- 3
# 删掉上游带过来、但本仓不该跑的 workflow。
# merge -X theirs 会把它们加回来，所以每次同步后必须再删一次。
#   - checkin.yml  上游的 GitHub Actions 定时签到：依赖 MIYOUQIAN_CONFIG /
#                  MIYOUQIAN_CREDENTIALS 两个本仓没有的 secret；而且本仓的签到
#                  跑在自己的容器里，GitHub 再跑一份会互相顶掉、白烧验证码。
#   - docker.yml   上游推 ghcr.io/marchen-orz/miyouqian：本仓属主是
#                  zhuweijun0121-dotcom，对那个 package 没有写权限，每次 push
#                  都必然失败。本仓的镜像由 sync-upstream.yml 推 Docker Hub。
echo "=== 3) 清理上游带过来的无关 workflow ==="
mkdir -p .github/workflows
for f in .github/workflows/checkin.yml .github/workflows/docker.yml; do
  if [ -f "$f" ]; then
    rm -f "$f"
    echo "   已删除 $f"
  fi
done
for f in .github/workflows/*.yml .github/workflows/*.yaml; do
  [ -e "$f" ] || continue
  case "$f" in
    .github/workflows/sync-upstream.yml) ;;
    *) fail "出现了预期外的工作流文件：$f（要么加进白名单，要么在 patch.sh 里删掉）" ;;
  esac
done
echo "✅ 只剩 sync-upstream.yml"

# ---------------------------------------------------------------- 4
echo "=== 4) 校验默认配置可用 ==="
python3 - <<'PY' || fail "mirror/config.default.yaml 不是合法 YAML"
import yaml
d = yaml.safe_load(open("mirror/config.default.yaml", encoding="utf-8"))
chans = (d.get("captcha") or {}).get("channels") or []
custom = [c for c in chans if c.get("provider") == "custom" and c.get("enable")]
assert custom, "默认配置里没有启用 custom 验证码渠道"
assert custom[0].get("url"), "custom 渠道缺少 url"
print(f"   验证码默认渠道: custom -> {custom[0]['url']}")
PY
echo "✅ 默认配置校验通过"

echo "🎉 全部改造已应用"
