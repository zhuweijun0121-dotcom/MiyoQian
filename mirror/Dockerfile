# 本仓（个人镜像仓）的构建文件 —— 由 patch.sh 覆盖到 docker/Dockerfile。
#
# 为什么不直接改 docker/Dockerfile：上游每次更新都会把它合并回来，
# 手工改动会在下一次同步时被 -X theirs 覆盖或产生冲突。所以真正的版本在这里，
# 同步流程末端由 patch.sh 强制铺回去，保证 docker/Dockerfile 恒等于本文件。
#
# 相对上游的改动：
#   1. 依赖全部随仓库自带在 vendor/wheels/，`pip install --no-index` 离线安装。
#      → 构建过程不访问 PyPI，也不用 ghcr.io 上的 uv 镜像（国内常被墙）。
#      → 只要能从 Docker Hub 拉到基础镜像，国内网络（或开着代理）都能构建。
#   2. 基础镜像用 ARG 暴露，需要时可换镜像加速地址。
#   3. 额外塞一份 mirror/config.default.yaml 进镜像，首次启动时用它当默认配置
#      （验证码默认走自建 gt-solver），见 mirror/entrypoint.sh。
#   4. 构建期把「本仓 HEAD 的 commit」烤进镜像（ARG → ENV MIYOUQIAN_BUILD_COMMIT / LABEL）。
#      本仓的「检查更新」改为查询 zhuweijun0121-dotcom/MiyoQian（见 patch.sh 的 updater 补丁），
#      比对的就是这个值。没有 docker.sock 时它读不到本地镜像标签，所以必须烤进来；
#      而且必须是本仓的 commit 而不是上游 sha —— 本仓 HEAD 是 CI 的 chore 提交，
#      与上游 sha 永不相等，用上游 sha 会恒报「有更新」。
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG BUILD_COMMIT=""
ARG BUILD_TIME=""
FROM ${PYTHON_IMAGE}

# ARG 在 FROM 之后要重新声明，否则 THIS 阶段读不到值
ARG BUILD_COMMIT
ARG BUILD_TIME

# 供上游 updater 的 get_local_version() 读取（它优先读这个环境变量）
ENV MIYOUQIAN_BUILD_COMMIT=${BUILD_COMMIT} \
    MIYOUQIAN_BUILD_TIME=${BUILD_TIME}

LABEL org.opencontainers.image.source="https://github.com/zhuweijun0121-dotcom/MiyoQian" \
      org.opencontainers.image.revision="${BUILD_COMMIT}" \
      org.opencontainers.image.created="${BUILD_TIME}"

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# vendor/wheels 只有 linux/amd64 的 wheel，换架构会装不上
RUN if [ "$(uname -m)" != "x86_64" ]; then \
        echo "vendor/wheels 仅含 x86_64 wheel；如需 arm64 请先跑 scripts/vendor-sync.sh 扩展平台"; \
        exit 1; \
    fi

# 先只放依赖，利用 Docker 缓存层
COPY vendor/wheels/ /tmp/wheels/
COPY vendor/requirements.lock.txt /tmp/requirements.lock.txt
RUN pip install --no-index --find-links=/tmp/wheels -r /tmp/requirements.lock.txt \
    && rm -rf /tmp/wheels /tmp/requirements.lock.txt

# 复制项目源码
COPY miyouqian/ ./miyouqian/
COPY main.py ./
COPY config.example.yaml ./
COPY mirror/config.default.yaml /app/config.default.yaml
COPY docker/entrypoint.sh /entrypoint.sh
# 对entrypoint.sh做兼容性修复，防止windows环境下因CRLF问题报错。
RUN sed -i 's/\r$//' /entrypoint.sh

RUN groupadd --system myq \
    && useradd --system --gid myq --home-dir /app --shell /usr/sbin/nologin myq \
    && mkdir -p /app/state/data /app/state/logs \
    && chmod 755 /entrypoint.sh \
    && chown -R myq:myq /app /app/state /entrypoint.sh

EXPOSE 5890

VOLUME ["/app/state"]

USER myq

HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5890/api/auth/status', timeout=3).read()"

ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py", "--config", "/app/state/config.yaml", "serve", "--host", "0.0.0.0", "--port", "5890"]
