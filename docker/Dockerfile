FROM python:3.11-slim

WORKDIR /app

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# 先复制依赖文件，利用 Docker 缓存层
COPY pyproject.toml uv.lock ./

# 安装依赖（不包含开发依赖）
RUN uv sync --frozen --no-dev

# 复制项目源码
COPY miyouqian/ ./miyouqian/
COPY main.py .
COPY config.example.yaml .
COPY docker/entrypoint.sh /entrypoint.sh

RUN chmod +x /entrypoint.sh

EXPOSE 5890

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["uv", "run", "python", "main.py", "serve", "--host", "0.0.0.0", "--port", "5890"]
