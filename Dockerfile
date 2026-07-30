# =============================================================================
# Multi-stage Dockerfile for fastapi-demo
#
# 注:不用 # syntax=docker/dockerfile 指令(避免纯内网拉 frontend 镜像卡住)。
# 现代 Docker(20.10+/24+)默认 BuildKit 已支持 --mount=type=cache。
#
# 公司内网部署说明:
#   - 基础镜像走公司镜像源(默认 python:3.11-slim,CI 时通过 --build-arg 覆盖)
#   - uv 从公司私有 PyPI 源装包(通过 UV_INDEX_URL build arg)
#   - 本地构建(公网)直接 docker build 即可;CI 构建传 build args 指向内网
#
#   # 本地(公网)构建:
#   docker build -t fastapi-demo:latest .
#
#   # CI / 内网构建(示例):
#   docker build \
#     --build-arg PYTHON_BASE_IMAGE=harbor.example.com/library/python:3.11-slim \
#     --build-arg UV_INDEX_URL=https://pypi.internal.example.com/simple \
#     --build-arg UV_INSTALLER_URL=https://files.internal.example.com/uv/install.sh \
#     -t harbor.example.com/your-group/fastapi-demo:latest .
#
# 配置 / prompts 通过卷挂载,无需重新构建镜像即可改配置。
# =============================================================================

# 基础镜像:默认 Docker Hub 公网;CI 时覆盖为公司镜像源。
ARG PYTHON_BASE_IMAGE=python:3.11-slim
# uv 安装脚本地址:默认 astral.sh 公网;CI 时覆盖为公司内网镜像。
ARG UV_INSTALLER_URL=https://astral.sh/uv/install.sh

# -----------------------------------------------------------------------------
# Stage 1: builder — 安装依赖,编译 C 扩展(asyncpg / bcrypt / hiredis / orjson)
# -----------------------------------------------------------------------------
FROM ${PYTHON_BASE_IMAGE} AS builder

# uv 私有 PyPI 源(通过 ARG 传入,默认公网 PyPI)。
# 也支持带认证的源:https://user:pass@pypi.internal.example.com/simple
ARG UV_INDEX_URL=""
ARG UV_INDEX_USER=""
ARG UV_INDEX_PASSWORD=""

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

# 安装 uv(从 UV_INSTALLER_URL 拉取;CI 时指向公司内网镜像)
ADD --chmod=755 ${UV_INSTALLER_URL} /tmp/uv-install.sh
RUN /tmp/uv-install.sh && rm /tmp/uv-install.sh

# 配置 uv:走私有 PyPI 源 + 编译字节码 + 不自动下 Python
ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/root/.local/bin:${PATH}"
# 若提供了私有源,设为默认 index-url。
# 带认证时把 user:password 嵌入 URL(https://user:pass@host/simple)。
RUN if [ -n "$UV_INDEX_URL" ]; then \
        if [ -n "$UV_INDEX_USER" ]; then \
            PROTO=$(echo "$UV_INDEX_URL" | sed -n 's#^\(https\?://\).*#\1#p'); \
            REST=$(echo "$UV_INDEX_URL" | sed 's#^https\?://##'); \
            uv config set index-url "${PROTO}${UV_INDEX_USER}:${UV_INDEX_PASSWORD}@${REST}"; \
        else \
            uv config set index-url "$UV_INDEX_URL"; \
        fi; \
    fi

WORKDIR /app

# 先拷元数据再装依赖 —— 利用 Docker 层缓存加速重复构建。
COPY pyproject.toml ./
COPY uv.lock* ./
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
FROM ${PYTHON_BASE_IMAGE} AS runtime

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

# 拷贝应用代码、配置、prompts、skills。
# config/ 和 prompts/ 也可以用 -v 卷挂载来覆盖,这里提供默认值。
COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser prompts/ ./prompts/
# skills 目录(agent skill 集成需要,如 code_review/SKILL.md)
COPY --chown=appuser:appuser skills/ ./skills/

# 切换非 root 用户
USER appuser

EXPOSE 18555

# 健康检查(容器层):调用 /health,失败 3 次重启容器
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:18555/health" || exit 1

# 用 fastapi CLI 启动生产模式(等价于 uvicorn app.main:app --host 0.0.0.0 --port 18555)。
# 实际容器里没有源码热重载需求,production 模式更稳。
# 如需 workers 数量,改 `fastapi run --workers 4`。
CMD ["fastapi", "run", "--host", "0.0.0.0", "--port", "18555"]
