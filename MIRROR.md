# 本仓说明（个人镜像仓）

这个仓库不是上游项目的 fork 按钮产物，而是一个**能直接跑的成品镜像仓**：

- 上游源码：<https://github.com/Marchen-orz/MiyoQian> —— 每 8 小时自动合并一次，`miyouqian/` 业务代码与上游**完全一致**，不打任何业务补丁
- 镜像：`zhu822560687/miyouqian`
- 唯一多做两件事：把「下载依赖」提前做好；把验证码默认指向自建的 gt-solver

## 为什么需要这个仓

上游 `docker/Dockerfile` 构建时要访问两个境外源：

| 依赖 | 上游做法 | 国内问题 |
|---|---|---|
| uv 二进制 | `COPY --from=ghcr.io/astral-sh/uv:0.7.3` | ghcr.io 经常连不上 |
| Python 包 | `uv sync` → PyPI | 慢、易断 |

而 apt / Docker Hub 这些又反而是国内直连更快。于是「开着代理」和「关着代理」总有一边出问题。

本仓的做法：**把 Python 依赖预下载进仓库**，用 pip 离线安装。

```
vendor/wheels/*.whl             # 13 个 wheel，约 10MB，cp311 / linux amd64
vendor/requirements.lock.txt    # 由 wheel 反推的精确版本
vendor/requirements.sha256      # 判断 requirements.txt 有没有变
scripts/vendor-sync.sh          # 重新下载（需要外网）
scripts/vendor-check.sh         # CI 用：变了才重下
```

`docker/Dockerfile` 改成 `pip install --no-index --find-links=vendor/wheels`，
不再用 uv、不再访问 PyPI。**构建只需要能从 Docker Hub 拉到 `python:3.11-slim-bookworm`。**

### 关于 uv（为什么去掉）

uv 是 Astral 出的 Rust 版 pip 替代品，不只是下载器。上游用它做三件事：
解析依赖、按 `uv.lock` 建虚拟环境、把包装进 `/opt/venv`。
它确实又快又能锁版本，但代价是构建时必须先拿到 `ghcr.io/astral-sh/uv` 那个镜像——
这一步在国内经常挂，而且和「外网 PyPI」是两处独立的失败点。

既然本仓已经把「解析结果」固化成 `vendor/requirements.lock.txt`、把「包」固化进
`vendor/wheels`，uv 在这条链上就没有额外价值了，所以去掉。
（上游的 `uv.lock` 仍留在仓库里，没删；`pyproject.toml` 也是上游原样。）

## 目录结构（本仓新增的部分）

```
mirror/Dockerfile           # 真正用于构建的 Dockerfile（patch.sh 铺到 docker/Dockerfile）
mirror/entrypoint.sh        # 真正用于启动的 entrypoint（patch.sh 铺到 docker/entrypoint.sh）
mirror/config.default.yaml  # 首次启动的默认配置：验证码默认走 gt-solver
patch.sh                    # 同步上游后执行：铺回上面三个文件并校验
docker-compose.yml          # 成品部署文件
scripts/test-gt-solver.py   # 自检：拿真实米游社验证码打一次 gt-solver
```

**为什么用 patch.sh 而不是直接改 `docker/Dockerfile`**：那文件上游也在改，每次同步
`git merge -X theirs` 会把它覆盖回来，手工改动会丢。所以真正的版本放在 `mirror/` 下，
同步流程末端强制铺回，保证 `docker/Dockerfile` 恒等于 `mirror/Dockerfile`。

## 验证码：默认按死自建 gt-solver

`mirror/config.default.yaml` 里验证码只启用一个渠道：

```yaml
captcha:
  max_retries: 5
  channels:
    - provider: custom
      enable: true
      url: "http://host.docker.internal:10721/custom"
      timeout: 120
```

对应 gt-solver 的「自定义接口协议」（规范见上游 `miyouqian/webui/custom-captcha-api.md`）：

```http
GET /custom?gt=<gt>&challenge=<challenge>
成功 → {"code":0,"message":"ok","data":{"challenge":"..","validate":".."}}
失败 → {"code":50001,"message":"..","data":null}
```

容器需要能访问宿主机 10721，`docker-compose.yml` 已加了：

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

如果 gt-solver 也在容器里、和本服务同一 docker 网络，把 url 改成
`http://<gt-solver 服务名>:10721/custom`（首次启动后在 Web 控制台改）。

自检（真实米游社验证码，不需要登录）：

```bash
bash scripts/test-gt-solver.sh
# 容器内：
docker exec -e GT_SOLVER_URL=http://host.docker.internal:10721/custom <容器名> python3 scripts/test-gt-solver.py
```

## 部署

```bash
git clone https://github.com/zhuweijun0121-dotcom/MiyoQian.git
cd MiyoQian
docker compose up -d          # 拉 zhu822560687/miyouqian:latest 并起服务
```

- 配置/凭证/日志都在 `./state`，重建容器不丢；首次启动用 `mirror/config.default.yaml` 生成 `./state/config.yaml`
- 端口 5890，Web 控制台首次访问时设置密码
- 账号到 Web 控制台扫码登录
- 更新：`docker compose pull && docker compose up -d`

## 镜像 tag 规则

上游不发版本号，所以本仓也不自造 `vX.Y.Z`：

| tag | 含义 |
|---|---|
| `latest` | 最近一次成功构建 |
| `sha-<7位>` | 对应上游某次提交，如 `sha-b93a53d` |
| `YYYYMMDD` | 构建日期，如 `20261126` |

## 本地构建

```bash
docker build -f docker/Dockerfile -t miyouqian:local .
# 换基础镜像加速地址：
docker build -f docker/Dockerfile --build-arg PYTHON_IMAGE=<加速地址>/library/python:3.11-slim-bookworm -t miyouqian:local .
```

构建过程不需要外网（`--network=none` 也能过），只要基础镜像在本地。

## 手动触发构建

GitHub → Actions → 「同步上游并构建镜像」→ Run workflow，勾 `force_build` 可跳过更新检查强制重建。

## 只支持 linux/amd64

`vendor/wheels` 里只有 x86_64 的 wheel。要上 arm64，先改 `scripts/vendor-sync.sh`
里的平台参数（加 `--platform manylinux_2_28_aarch64`）并提交新的 wheel。
