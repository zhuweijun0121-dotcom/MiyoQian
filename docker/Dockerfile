# 构建镜像。
#
# 本仓相对上游的改动（构建相关）：
#   1. 依赖全部随仓库自带在 vendor/wheels/，`pip install --no-index` 离线安装。
#      → 构建过程不访问 PyPI，也不再需要 ghcr.io 上的 uv 镜像（国内常被墙）。
#      → 只要能从 Docker Hub 拉到基础镜像，国内网络（或开着代理）都能构建。
#   2. 基础镜像用 ARG 暴露，需要时换镜像加速地址：
#      docker build --build-arg PYTHON_IMAGE=<你的加速地址>/python:3.11-slim-bookworm .
#   3. 依赖更新不要手改 wheels：跑 scripts/vendor-sync.sh（需外网）；
#      或者在 CI 里改 requirements.txt，流水线会自动重新 vendor。
ARG PYTHON_IMAGE=python:3.11-slim-bookworm
FROM ${PYTHON_IMAGE}

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
