# 部署指南 — JFrog 镜像仓库 + Docker Compose 自动部署

用 `deploy.sh` 一键部署:登录 JFrog → 拉镜像 → 数据库迁移 → 启动服务。

## 一、镜像构建并推送到 JFrog

在开发机(能访问 JFrog)上构建并推送:

```bash
# 构建镜像(本地公网环境)
docker build -t your-jfrog.company.com/docker-local/fastapi-demo:latest .

# 登录 JFrog
docker login your-jfrog.company.com -u 用户名 -p 密码

# 推送
docker push your-jfrog.company.com/docker-local/fastapi-demo:latest

# 发版本时打 tag 推送
docker tag your-jfrog.company.com/docker-local/fastapi-demo:latest \
           your-jfrog.company.com/docker-local/fastapi-demo:v1.0.0
docker push your-jfrog.company.com/docker-local/fastapi-demo:v1.0.0
```

---

## 二、生产服务器部署(用 deploy.sh)

### 1. 准备服务器(一次性)

```bash
# 安装 docker + compose 插件
curl -fsSL https://get.docker.com | sh

# 拉取代码
git clone <仓库地址> /opt/fastapi-demo
cd /opt/fastapi-demo
```

### 2. 配置 .env 和 config/local.yaml

```bash
cp .env.example .env
vi .env                # 填 JFrog 地址/账号/密码、镜像名、数据库密码
```

`.env` 关键配置:
```ini
REGISTRY=your-jfrog.company.com          # JFrog 地址(不带协议)
REGISTRY_USER=用户名
REGISTRY_PASSWORD=密码
IMAGE_NAME=your-jfrog.company.com/docker-local/fastapi-demo
IMAGE_TAG=latest                         # 或 v1.0.0
POSTGRES_PASSWORD=数据库密码
```

```bash
vi config/local.yaml    # 填 LLM provider(qwen key)、数据源密码
                        # host 用服务名:postgres / redis
```

### 3. 一键部署

```bash
./deploy.sh deploy
```

`deploy` 会自动完成:登录 JFrog → 拉镜像 → 启动 postgres/redis → 数据库迁移 → 启动 app → 清理旧镜像。

---

## 三、deploy.sh 子命令

| 命令 | 作用 |
|------|------|
| `./deploy.sh deploy` | **完整部署**(login + pull + migrate + up),最常用 |
| `./deploy.sh up` | 只启动/更新服务(不拉镜像) |
| `./deploy.sh pull` | 只登录 + 拉镜像 |
| `./deploy.sh migrate` | 只跑数据库迁移(alembic upgrade head) |
| `./deploy.sh restart` | 重启 app 容器 |
| `./deploy.sh stop` | 停止所有服务 |
| `./deploy.sh logs` | 跟踪 app 日志 |
| `./deploy.sh status` | 查看服务状态 |
| `./deploy.sh clean` | 停止并删除容器(保留数据卷) |

---

## 四、更新到新版本

```bash
# 1. 改 .env 里的 IMAGE_TAG 为新版本(如 v1.0.1)
vi .env
#   IMAGE_TAG=v1.0.1

# 2. 重新部署
./deploy.sh deploy
```

`deploy` 会自动拉新镜像并重启。数据库迁移有标记机制:同一个 tag 只迁移一次(避免重复跑),强制迁移用 `./deploy.sh migrate`。

---

## 五、AI Agent 框架的生产配置要点

`config/local.yaml` 里 agents 段:

```yaml
agents:
  enabled: true
  session_datasource: "postgres_primary"  # 会话/运行记录存这里

  # 持久记忆(可选,需独立 PG + pgvector 扩展)
  persistent_memory:
    enabled: false                        # 不用就关;用则填独立向量库数据源
    datasource: "postgres_vector"
    embedding_provider: "qwen"
```

> 持久记忆的 `agent_memories` 表依赖 pgvector 扩展,需在独立 PG 上执行:
> `CREATE EXTENSION IF NOT EXISTS vector;`
> 默认迁移(env.py 的 include_object)只在 persistent_memory.datasource == 目标库时建该表。

---

## 六、首次部署后的数据库迁移

```bash
# 进 app 容器跑迁移(建表:items / agent_sessions / agent_runs / agent_memories)
./deploy.sh migrate
# 或手动
docker compose -f docker-compose.prod.yml exec app alembic upgrade head
```

---

## 七、常见问题

**Q: `docker login` 失败?**
检查 `.env` 的 `REGISTRY`/`REGISTRY_USER`/`REGISTRY_PASSWORD` 是否正确,网络能否访问 JFrog。

**Q: 镜像拉取超时?**
确认 JFrog 里有对应镜像(`IMAGE_NAME:IMAGE_TAG`),且 `IMAGE_NAME` 路径写对。

**Q: 基础镜像(postgres/redis)拉不到?**
默认从 Docker Hub 拉。若服务器不能访问 Docker Hub,在 `.env` 里指定 JFrog 内的镜像:
```ini
POSTGRES_IMAGE=your-jfrog.company.com/docker-proxy/postgres:16-alpine
REDIS_IMAGE=your-jfrog.company.com/docker-proxy/redis:7-alpine
```

**Q: 怎么看部署是否成功?**
```bash
./deploy.sh status    # 服务状态
./deploy.sh logs      # app 日志,看 "All datasources, LLM, agent gateway ... ready"
curl http://localhost:8000/health   # 健康检查
```
