# syntax=docker/dockerfile:1.7
# =============================================================================
# Multi-stage Dockerfile for fastapi-demo
#
# 构建产物可直接 docker save 成 tar 文件,拷贝到目标机器加载运行。
#
#   docker build -t fastapi-demo:latest .
#   docker save fastapi-demo:latest -o fastapi-demo.tar
#   # 目标机器:
#   docker load -i fastapi-demo.tar
#   docker run --rm -p 8000:8000 -v $(pwd)/config:/app/config fastapi-demo:latest
#
# 配置 / prompts 通过卷挂载,无需重新构建镜像即可改配置。
# =============================================================================

ARG PYTHON_VERSION=3.11-slim

# -----------------------------------------------------------------------------
# Stage 1: builder — 安装依赖,编译 C 扩展(asyncpg / bcrypt / hiredis / orjson)
# -----------------------------------------------------------------------------
FROM ${PYTHON_VERSION} AS builder

# builder 阶段需要 build-essential 来编译 C 扩展。
# hadolint ignore=DL3008
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv(官方 skill 推荐,Astral 官方镜像脱壳方式)
ADD --chmod=755 https://astral.sh/uv/install.sh /tmp/uv-install.sh
RUN /tmp/uv-install.sh && rm /tmp/uv-install.sh
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/root/.local/bin:${PATH}"

WORKDIR /app

# 先拷元数据再装依赖 —— 利用 Docker 层缓存加速重复构建。
COPY pyproject.toml ./
# 提供一个最小的 app/__init__.py 以便 `uv pip install -e .` 能解析本地包。
COPY app/__init__.py app/__init__.py
COPY app/py.typed app/py.typed

# 装运行时依赖到一个独立目录(后续拷到 runtime 阶段)。
# 注意:不带 [dev],镜像里不需要 pytest/ruff/mypy。
RUN --mount=type=cache,target=/root/.cache/uv \
    uv venv --python 3.11 /opt/venv \
    && VIRTUAL_ENV=/opt/venv uv pip install --no-cache .

# -----------------------------------------------------------------------------
# Stage 2: runtime —— 精简镜像,只装运行时必需的系统库
# -----------------------------------------------------------------------------
FROM ${PYTHON_VERSION} AS runtime

# 运行时不再需要编译器,只需少量运行库(libpq 给 psycopg/asyncpg 间接依赖,
# 实际 asyncpg 是纯 cython 自带 — 这里保留 ca-certificates 给 httpx/openai 用)。
# hadolint ignore=DL3008
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/* \
    # 创建非 root 用户
    && groupadd --system --gid 1001 appuser \
    && useradd --system --uid 1001 --gid appuser --create-home --shell /usr/sbin/nologin appuser

# 从 builder 拷贝虚拟环境(已编译好的依赖)
COPY --from=builder /opt/venv /opt/venv

# 让 venv 里的 python / fastapi / uvicorn 在 PATH 最前
ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 拷贝应用代码、配置、prompts。
# config/ 和 prompts/ 也可以用 -v 卷挂载来覆盖,这里提供默认值。
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser prompts/ ./prompts/

# 切换非 root 用户
USER appuser

EXPOSE 8000

# 健康检查(容器层):调用 /health,失败 3 次重启容器
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:8000/health" || exit 1

# 用 fastapi CLI 启动生产模式(等价于 uvicorn app.main:app --host 0.0.0.0 --port 8000)。
# 实际容器里没有源码热重载需求,production 模式更稳。
# 如需 workers 数量,改 `fastapi run --workers 4`。
CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "8000"]
