#!/usr/bin/env bash
# =============================================================================
# 自动部署脚本 —— 在生产服务器上运行
#
# 用法:
#   1. 把代码 clone 到服务器: git clone <repo> /opt/fastapi-demo && cd /opt/fastapi-demo
#   2. 配置 .env:cp .env.example .env && vi .env  (填 JFrog 地址/账号/镜像名等)
#   3. 配置 config/local.yaml(数据库密码、LLM key 等)
#   4. 部署:./deploy.sh deploy
#
# 子命令:
#   deploy    登录 JFrog → 拉最新镜像 → (首次)跑迁移 → 启动/更新服务
#   up        只启动/更新服务(不拉镜像、不登录)
#   pull      只登录 + 拉镜像
#   migrate   进 app 容器跑 alembic 数据库迁移
#   restart   重启 app 容器
#   stop      停止所有服务
#   logs      跟踪 app 日志
#   status    查看服务状态
#   clean     停止并删除容器(保留数据卷)
#
# 配置全部从同目录的 .env 读取(密码不进命令行历史)。
# =============================================================================
set -euo pipefail

# ---------- 颜色输出 ----------
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[OK]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
die()   { echo -e "${RED}[ERROR]${NC} $*" >&2; exit 1; }

# ---------- 定位项目根(脚本所在目录) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE_FILE="docker-compose.prod.yml"
ENV_FILE=".env"

# ---------- 加载 .env ----------
if [[ ! -f "$ENV_FILE" ]]; then
    die ".env 不存在。请先执行: cp .env.example $ENV_FILE && vi $ENV_FILE"
fi
# shellcheck disable=SC1090
set -a; source "$ENV_FILE"; set +a

# 校验必填变量
: "${REGISTRY:?在 .env 里缺少 REGISTRY(JFrog 镜像仓库地址)}"
: "${REGISTRY_USER:?在 .env 里缺少 REGISTRY_USER}"
: "${REGISTRY_PASSWORD:?在 .env 里缺少 REGISTRY_PASSWORD}"
: "${IMAGE_NAME:?在 .env 里缺少 IMAGE_NAME(完整镜像路径)}"

IMAGE_TAG="${IMAGE_TAG:-latest}"
COMPOSE="docker compose -f $COMPOSE_FILE --env-file $ENV_FILE"

# ---------- 前置检查 ----------
check_prereqs() {
    command -v docker >/dev/null 2>&1 || die "未安装 docker"
    docker info >/dev/null 2>&1 || die "docker daemon 未运行,请先启动 docker"
    # docker compose v2(插件)优先;v1(docker-compose)兜底
    if docker compose version >/dev/null 2>&1; then
        :
    elif command -v docker-compose >/dev/null 2>&1; then
        COMPOSE="docker-compose -f $COMPOSE_FILE --env-file $ENV_FILE"
        warn "使用 docker-compose v1,建议升级到 docker compose v2"
    else
        die "未安装 docker compose(运行: docker compose version 检查)"
    fi
}

# ---------- 登录 JFrog 镜像仓库 ----------
do_login() {
    info "登录 JFrog 镜像仓库: $REGISTRY"
    # 用 stdin 传密码,避免出现在 ps / 命令行历史
    echo "$REGISTRY_PASSWORD" | docker login "$REGISTRY" -u "$REGISTRY_USER" --password-stdin \
        || die "docker login 失败,请检查 REGISTRY/REGISTRY_USER/REGISTRY_PASSWORD"
    ok "已登录 $REGISTRY"
}

# ---------- 拉取镜像 ----------
do_pull() {
    do_login
    info "拉取镜像 $IMAGE_NAME:$IMAGE_TAG"
    $COMPOSE pull
    ok "镜像拉取完成"
}

# ---------- 数据库迁移 ----------
do_migrate() {
    info "运行数据库迁移(alembic upgrade head)"
    # 确保 app 容器在跑,迁移在容器内执行
    $COMPOSE up -d --no-deps app >/dev/null 2>&1 || true
    $COMPOSE exec -T app alembic upgrade head \
        || die "迁移失败。可手动排查: $COMPOSE exec app alembic upgrade head"
    ok "迁移完成"
}

# ---------- 部署主流程 ----------
do_deploy() {
    check_prereqs
    info "部署镜像: $IMAGE_NAME:$IMAGE_TAG"

    # 1. 登录 + 拉镜像
    do_pull

    # 2. 启动依赖(postgres/redis)并等待健康
    info "启动数据源服务(postgres/redis)"
    $COMPOSE up -d postgres redis

    # 3. 首次部署或新增表时跑迁移(用文件标记是否已迁移过本次 tag)
    MIGRATE_MARKER="logs/.migrated-${IMAGE_TAG}"
    mkdir -p logs
    if [[ ! -f "$MIGRATE_MARKER" ]]; then
        do_migrate && touch "$MIGRATE_MARKER" || warn "迁移标记未写入,下次 deploy 会重试迁移"
    else
        info "本次 tag($IMAGE_TAG)已迁移过,跳过(强制迁移用: ./deploy.sh migrate)"
    fi

    # 4. 启动/更新 app
    info "启动 app 服务"
    $COMPOSE up -d app

    # 5. 清理旧镜像(可选,省磁盘)
    if [[ "${PRUNE_IMAGES:-yes}" == "yes" ]]; then
        info "清理悬空镜像(dangling)"
        docker image prune -f >/dev/null 2>&1 || true
    fi

    ok "部署完成! 查看状态: ./deploy.sh status  |  看日志: ./deploy.sh logs"
}

# ---------- 子命令分发 ----------
cmd="${1:-deploy}"
case "$cmd" in
    deploy)  check_prereqs; do_deploy ;;
    up)      check_prereqs; info "启动服务"; $COMPOSE up -d ;;
    pull)    check_prereqs; do_pull ;;
    migrate) check_prereqs; do_migrate ;;
    restart) check_prereqs; info "重启 app"; $COMPOSE restart app ;;
    stop)    check_prereqs; info "停止所有服务"; $COMPOSE down ;;
    logs)    check_prereqs; $COMPOSE logs -f --tail=200 app ;;
    status)  check_prereqs; $COMPOSE ps ;;
    clean)   check_prereqs; warn "将停止并删除容器(保留数据卷)"; $COMPOSE down ;;
    *)       die "未知命令: $cmd
可用命令: deploy | up | pull | migrate | restart | stop | logs | status | clean" ;;
esac
