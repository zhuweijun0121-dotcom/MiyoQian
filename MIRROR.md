# 本仓说明（个人镜像仓）

这个仓库不是上游项目的 fork 按钮产物，而是一个**自动跟随上游的镜像仓**，用途只有一个：
在国内网络环境下，用最少的外部依赖把米游签构建成镜像并发布到 Docker Hub。

- 上游源码：<https://github.com/Marchen-orz/MiyoQian>（本仓每 8 小时自动合并一次，冲突一律取上游）
- 镜像地址：`zhu822560687/miyouqian`
- 本地补丁：`patch.sh`（目前为空 —— 需要的改动上游都已原生实现）

## 为什么需要这个仓

上游 `docker/Dockerfile` 构建时要访问两个境外源：

| 依赖 | 上游做法 | 国内问题 |
|---|---|---|
| uv 二进制 | `COPY --from=ghcr.io/astral-sh/uv:0.7.3` | ghcr.io 经常连不上 |
| Python 包 | `uv sync` → PyPI | 慢、易断 |

而 apt / Docker Hub 这些又是国内直连更快的。于是「开着代理」和「关着代理」总有一边出问题。

本仓的做法是**把 Python 依赖预下载进仓库**：

```
vendor/wheels/*.whl             # 13 个 wheel，约 11MB，cp311 / linux amd64
vendor/requirements.lock.txt    # 由 wheel 反推的精确版本
vendor/requirements.sha256      # 用来判断 requirements.txt 有没有变
scripts/vendor-sync.sh          # 重新下载（需要外网）
scripts/vendor-check.sh         # CI 用：变了才重下
```

`docker/Dockerfile` 改成 `pip install --no-index --find-links=vendor/wheels`，
不再用 uv、不再访问 PyPI。**构建只需要能拉到 `python:3.11-slim-bookworm` 这一个基础镜像。**

## 本地怎样构建

```bash
git clone https://github.com/zhuweijun0121-dotcom/MiyoQian.git
cd MiyoQian
docker build -f docker/Dockerfile -t miyouqian:local .
# 想换基础镜像加速地址：
docker build -f docker/Dockerfile --build-arg PYTHON_IMAGE=<加速地址>/library/python:3.11-slim-bookworm -t miyouqian:local .
```

## 怎样部署

```bash
docker pull zhu822560687/miyouqian:latest
```

`docker/docker-compose.image.yml` 是现成的 compose 文件（配置/凭证/日志都在 `/app/state` 卷里）。
注意镜像以非 root 用户 `myq` 运行、并且 `read_only: true`，挂载目录需要给它写权限，
或者在该 service 里加 `user: "0:0"`。

## 镜像 tag 规则

上游不发版本号，所以本仓也不自造 `vX.Y.Z`：

| tag | 含义 |
|---|---|
| `latest` | 最近一次成功构建 |
| `sha-<7位>` | 对应上游的某次提交，如 `sha-b93a53d` |
| `YYYYMMDD` | 构建日期，如 `20261126`，方便按天回滚 |

## 手动触发构建

GitHub → Actions → 「同步上游并构建镜像」→ Run workflow，勾 `force_build` 可跳过更新检查强制重建。

## 只支持 linux/amd64

`vendor/wheels` 里只有 x86_64 的 wheel。要上 arm64 机器，先改 `scripts/vendor-sync.sh`
里的平台参数（加 `--platform manylinux_2_28_aarch64`）并提交新的 wheel。
