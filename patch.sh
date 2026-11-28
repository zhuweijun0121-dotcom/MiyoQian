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
# 业务源码补丁：updater.py 两处，只改「指向」，不动更新算法本身。
#
# A. 镜像指向：上游把镜像写死成 ghcr.io/marchen-orz/miyoqian:latest（作者的）。
#    我们的容器跑的是 Docker Hub 的 zhu822560687/miyouqian:latest。没挂 docker.sock 时
#    上游拿那个硬编码值当「当前镜像」，于是 UI 显示的和提示你去拉的都成了作者的镜像。
#
# B. 检查更新的仓库：上游查 Marchen-orz/MiyoQian（作者仓），语义是「作者有没有发新代码」。
#    但对我们来说「能不能升级」取决于「本仓 CI 有没有构建出新镜像」，所以改查本仓。
#    注意：atom/api 查 GitHub、镜像查 Docker Hub 是两条独立通道 —— 查本仓才和镜像对得上。
#
#    这里有个必须一起成立的前提：镜像里烤的版本标识也得是「本仓 HEAD」，
#    而不是上游 sha（见 mirror/Dockerfile 与 workflow 的 BUILD_COMMIT）。
#    否则本仓 HEAD 是 CI 的 chore 提交、镜像里是上游 sha，两者永不相等 → 恒报「有更新」。
echo "=== 3) 补丁：updater.py（镜像指向本仓 + 检查更新查本仓）==="
python3 - <<'PY' || fail "updater.py 补丁失败"
import pathlib
import sys

p = pathlib.Path("miyouqian/core/updater.py")
if not p.is_file():
    sys.exit("找不到 miyouqian/core/updater.py（上游可能改了文件名）")

src = p.read_text(encoding="utf-8")
MIRROR = "zhu822560687/miyouqian:latest"
OWNER = "zhuweijun0121-dotcom"
done = []

# ---------------------------------------------------------------- A 镜像指向
if f'MIRROR_IMAGE = "{MIRROR}"' in src:
    print("   A) 镜像指向已是本仓，跳过")
else:
    # 1) 常量
    anchor = 'GHCR_DEFAULT_IMAGE = f"{GHCR_REGISTRY}/{GHCR_IMAGE_PATH}:latest"\n'
    if anchor not in src:
        sys.exit("找不到 GHCR_DEFAULT_IMAGE 定义，上游可能改动了该常量")
    src = src.replace(
        anchor,
        anchor
        + '\n# 本仓改造：容器实际运行的镜像（Docker Hub）。上游拿 GHCR_DEFAULT_IMAGE 当\n'
        + '# "当前镜像"兜底时会显示成作者的 ghcr 镜像，这里换成本仓的。\n'
        + f'MIRROR_IMAGE = "{MIRROR}"\n',
        1,
    )

    # 2) 无 docker.sock 时展示的"当前镜像"
    old = '        info["image"] = GHCR_DEFAULT_IMAGE\n'
    if old not in src:
        sys.exit('找不到 get_local_version 里的 info["image"] 赋值')
    src = src.replace(old, '        info["image"] = MIRROR_IMAGE\n', 1)

    # 3) 手动命令提示里的镜像是 local 的兜底
    old = 'build_docker_manual_command(local.get("image") or GHCR_DEFAULT_IMAGE, ghcr_mirror=ghcr_mirror)'
    if old not in src:
        sys.exit("找不到 manual_command 的调用点")
    src = src.replace(
        old,
        'build_docker_manual_command(local.get("image") or MIRROR_IMAGE, ghcr_mirror=ghcr_mirror)',
        1,
    )

    # 4) 真正执行更新时的镜像兜底
    old = 'or GHCR_DEFAULT_IMAGE).strip()'
    if old not in src:
        sys.exit("找不到 apply_docker_update 里的 current_image_ref 兜底")
    src = src.replace(old, 'or MIRROR_IMAGE).strip()', 1)

    # 5) Docker Hub 镜像不该被拼上 ghcr 加速源
    old_doc = '    """生成给未挂载 docker.sock 用户的宿主机更新命令提示。"""\n'
    if old_doc not in src:
        sys.exit("找不到 build_docker_manual_command 的文档字符串")
    src = src.replace(
        old_doc,
        old_doc
        + "    # 本仓改造：非 ghcr 镜像（如 Docker Hub）不需要加速源，直接给 compose 命令\n"
        + "    _repo, _tag = split_image_ref(image_ref or MIRROR_IMAGE)\n"
        + '    if not _repo.startswith(f"{GHCR_REGISTRY}/"):\n'
        + '        return "docker compose pull && docker compose up -d"\n',
        1,
    )
    done.append("镜像指向")

# ---------------------------------------------------------------- B 更新检查仓库
if f'GITHUB_OWNER = "{OWNER}"' in src:
    print("   B) 检查更新已指向本仓，跳过")
else:
    old = 'GITHUB_OWNER = "Marchen-orz"'
    if old not in src:
        sys.exit("找不到 GITHUB_OWNER 定义，上游可能改动了该常量")
    src = src.replace(old, f'GITHUB_OWNER = "{OWNER}"', 1)

    # ghcr 兜底探测路径必须写死上游：本仓不往 ghcr 发布任何包。
    # （GITHUB_OWNER 改成我们之后，这一行若仍用 f-string 推导会得到一个不存在的路径）
    old_ghcr = 'GHCR_IMAGE_PATH = f"{GITHUB_OWNER.lower()}/{GITHUB_REPO_NAME.lower()}"'
    if old_ghcr not in src:
        sys.exit("找不到 GHCR_IMAGE_PATH 定义")
    src = src.replace(
        old_ghcr,
        "# 本仓改造：ghcr 兜底探测仍指上游（本仓不发布到 ghcr；镜像走 MIRROR_IMAGE）\n"
        'GHCR_IMAGE_PATH = "marchen-orz/miyouqian"',
        1,
    )
    done.append("检查更新仓库")

if done:
    p.write_text(src, encoding="utf-8")
    print("   (已应用：%s)" % "、".join(done))

final = p.read_text(encoding="utf-8")
for needle in (
    f'MIRROR_IMAGE = "{MIRROR}"',
    'info["image"] = MIRROR_IMAGE',
    'or MIRROR_IMAGE).strip()',
    'local.get("image") or MIRROR_IMAGE',
    f'GITHUB_OWNER = "{OWNER}"',
    'GHCR_IMAGE_PATH = "marchen-orz/miyouqian"',
    'GITHUB_REPO = f"{GITHUB_OWNER}/{GITHUB_REPO_NAME}"',
    'GITHUB_ATOM_URL = f"https://github.com/{GITHUB_REPO}/commits/{GITHUB_BRANCH}.atom"',
):
    if needle not in final:
        sys.exit(f"补丁校验失败：缺少 {needle}")
for bad in ('info["image"] = GHCR_DEFAULT_IMAGE', 'GITHUB_OWNER = "Marchen-orz"'):
    if bad in final:
        sys.exit(f"补丁校验失败：仍残留 {bad}")
print("   ✅ 校验通过（GITHUB_REPO = %s/MiyoQian）" % OWNER)
PY
python3 -m py_compile miyouqian/core/updater.py || fail "updater.py 语法检查未通过"
echo "✅ updater.py 补丁完成，语法检查通过"

# ---------------------------------------------------------------- 3b
# 业务源码补丁 C：去掉「ghcr 镜像仓库」这个兜底源。
#
# 上游源顺序：自定义加速源 → GitHub atom → GitHub API → gh-proxy 加速源 →
# ghcr 镜像仓库（ghcr.nju.edu.cn/marchen-orz/miyouqian）。
# 最后那个读的是【上游镜像】的 revision 标签 = 上游 sha，而本地烤的是【本仓 HEAD】，
# 两者永不相等 —— 一旦走到这个兜底，界面就误报「有更新」，点更新又拉不到东西。
#
# 为什么不改成读我们自己的 Docker Hub 镜像：实测这台机器上从容器内
# auth.docker.io / registry-1.docker.io 不可达（IPv6 直连 network unreachable、
# IPv4 超时）。所以直接去掉这个源 —— 所有 GitHub 源都失败时诚实地报「检查失败」，
# 而不是给一个错的答案。
echo "=== 3b) 补丁：去掉误导性的 ghcr 镜像兜底源 ==="
python3 - <<'PYCHK' || fail "updater.py 兜底源补丁失败"
import pathlib
import sys

p = pathlib.Path("miyouqian/core/updater.py")
src = p.read_text(encoding="utf-8")

MARK = "_ = custom_ghcr  # 配置项保留，但不再参与兜底"
NEW = (
    "    # 本仓改造：不再用 ghcr 镜像仓库做兜底。\n"
    "    # 它读的是上游镜像的 revision（上游 sha），与本地烤进去的本仓 HEAD 永不相等，\n"
    "    # 网络不好走到这里会误报「有更新」。无 GitHub 源可用时直接报检查失败。\n"
    "    " + MARK + "\n"
)
OLD = (
    "    oci_hosts: list[str] = []\n"
    "    if custom_ghcr:\n"
    "        oci_hosts.append(custom_ghcr)\n"
    "    if DEFAULT_GHCR_MIRROR not in oci_hosts:\n"
    "        oci_hosts.append(DEFAULT_GHCR_MIRROR)\n"
    "    for host in oci_hosts:\n"
    '        candidates.append((f"镜像仓库 ({host})", "oci", host))\n'
)

if MARK in src:
    print("   (已去掉 ghcr 兜底，跳过)")
elif OLD in src:
    src = src.replace(OLD, NEW, 1)
    p.write_text(src, encoding="utf-8")
    print("   (已去掉 ghcr 镜像兜底源)")
else:
    sys.exit("找不到 oci_hosts 构建块，上游可能改动了 fetch_remote_commits")

final = p.read_text(encoding="utf-8")
if MARK not in final:
    sys.exit("兜底源补丁校验失败：标记缺失")
if 'oci_hosts.append(DEFAULT_GHCR_MIRROR)' in final:
    sys.exit("兜底源补丁校验失败：ghcr 兜底仍在")
if 'candidates.append((f"镜像仓库 ({host})", "oci", host))' in final:
    sys.exit("兜底源补丁校验失败：仍会往候选里加镜像仓库")
print("   ✅ 校验通过（检查更新只查 GitHub 系源：自家仓 atom / API / gh-proxy）")
PYCHK
python3 -m py_compile miyouqian/core/updater.py || fail "兜底源补丁后语法检查未通过"
echo "✅ 兜底源补丁完成"

# ---------------------------------------------------------------- 4
# 删掉上游带过来、但本仓不该跑的 workflow。
# merge -X theirs 会把它们加回来，所以每次同步后必须再删一次。
#   - checkin.yml  上游的 GitHub Actions 定时签到：依赖 MIYOUQIAN_CONFIG /
#                  MIYOUQIAN_CREDENTIALS 两个本仓没有的 secret；而且本仓的签到
#                  跑在自己的容器里，GitHub 再跑一份会互相顶掉、白烧验证码。
#   - docker.yml   上游推 ghcr.io/marchen-orz/miyouqian：本仓属主是
#                  zhuweijun0121-dotcom，对那个 package 没有写权限，每次 push
#                  都必然失败。本仓的镜像由 sync-upstream.yml 推 Docker Hub。
echo "=== 4) 清理上游带过来的无关 workflow ==="
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

# ---------------------------------------------------------------- 5
echo "=== 5) 校验默认配置可用 ==="
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
