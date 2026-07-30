# 部署指南 — GitLab CI 构建 + Docker Compose 生产部署

本文档说明如何通过 GitLab CI 构建镜像并推送到公司 Harbor 仓库,然后在生产服务器用 docker compose 部署。

## 整体流程

```
  push main/tag                    打 tag 时触发
      │                                 │
      ▼                                 ▼
 GitLab CI 构建                GitLab CI 构建
 (docker build)              (docker build)
      │                                 │
      ▼                                 ▼
 推送 Harbor (latest+sha)    推送 Harbor (版本号)
                                        │
                                        ▼
                              SSH 到生产服务器
                              docker compose pull + up
```

---

## 一、GitLab 上配置 CI/CD 变量

在 GitLab 项目 **Settings → CI/CD → Variables** 添加以下变量:

### 必填(镜像仓库)

| 变量名 | 说明 | 示例 | Masked | Protected |
|--------|------|------|--------|-----------|
| `CI_REGISTRY` | 公司镜像仓库地址 | `harbor.example.com` | | ✓ |
| `CI_REGISTRY_USER` | 推送账号 | `ci-deployer` | | |
| `CI_REGISTRY_PASSWORD` | 推送密码/Token | `xxxx` | ✓ | ✓ |
| `CI_REGISTRY_IMAGE` | 镜像完整路径 | `harbor.example.com/your-group/fastapi-demo` | | ✓ |

### 必填(私有 PyPI 源 — uv 装包用)

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `UV_INDEX_URL` | 私有 PyPI 源地址 | `https://pypi.internal.example.com/simple` |
| `UV_INDEX_USER` | 私有 PyPI 用户名(无认证留空) | `ci` |
| `UV_INDEX_PASSWORD` | 私有 PyPI 密码(无认证留空) | `xxxx` |

### 必填(镜像基础资源)

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `PYTHON_BASE_IMAGE` | 公司源里的 Python 镜像 | `harbor.example.com/library/python:3.11-slim` |
| `UV_INSTALLER_URL` | uv 安装脚本内网地址 | `https://files.internal.example.com/uv/install.sh` |

### 部署阶段(自动部署生产用,打 tag 才需要)

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `DEPLOY_SSH_KEY` | 生产服务器 SSH 私钥 | (粘贴私钥内容) |
| `DEPLOY_SERVER` | 生产服务器(用户@地址) | `deploy@10.0.0.10` |
| `DEPLOY_PATH` | 服务器上 compose 目录 | `/opt/fastapi-demo` |

> **Masked**:日志中会遮盖显示。**Protected**:只在受保护分支/标签上可用。
> 建议所有密码类变量都勾选 Masked + Protected。

---

## 二、GitLab 上保护分支和标签

**Settings → Repository → Protected branches/tags**:

- Protected branch: `main`(只有 maintainer 能 push,CI 变量 Protected 才生效)
- Protected tags: `v*`(打版本号标签触发部署)

---

## 三、CI 流水线说明(.gitlab-ci.yml)

| 阶段 | 触发条件 | 作用 |
|------|---------|------|
| `lint` | Merge Request | ruff 代码检查 |
| `build` | main 分支 / tag | 构建镜像,推送 Harbor(版本 tag + latest) |
| `deploy` | 仅 tag | SSH 到生产服务器,pull + 重启 |

- main 分支 push:只构建推送,不部署(用于预发布/验证)
- 打 tag(如 `v1.0.0`):构建推送 + 自动部署生产

---

## 四、生产服务器一次性配置

### 1. 安装 Docker

```bash
# 安装 docker + compose 插件(按服务器系统选)
curl -fsSL https://get.docker.com | sh
# 公司内网可换内部源的 docker 安装包
```

### 2. 配置镜像仓库登录(只需一次)

```bash
# 用 CI 里同一个推送账号(或专门的拉取账号)
docker login harbor.example.com -u ci-deployer -p 'xxxx'
# 登录信息保存在 ~/.docker/config.json,后续 docker pull/compose 不用再登录
```

### 3. 准备部署目录和配置

```bash
mkdir -p /opt/fastapi-demo && cd /opt/fastapi-demo

# 把这些文件放到服务器(GitLab 仓库根目录里的):
#   - docker-compose.prod.yml
#   - .env          (cp .env.example .env 后填真实值)
#   - config/       (含 config.yaml + local.yaml)
#   - prompts/
#   - skills/       (可选;不挂卷则用镜像内置的)
```

### 4. 编辑 .env 和 config/local.yaml

```bash
cp .env.example .env
vi .env              # 填 IMAGE_NAME / IMAGE_TAG / 数据库密码等

vi config/local.yaml # 填 LLM provider(qwen key)、数据源密码(enc: 加密)、
                     #   host 用服务名(postgres/redis)或容器名
```

容器内 `local.yaml` 的数据源 host 用 docker 网络的服务名:

```yaml
datasources:
  postgres_primary:
    type: postgresql
    host: postgres          # docker-compose 服务名,不是 127.0.0.1
    port: 5432
    username: postgres
    password: "enc:..."     # 加密密码
    database: app
```

---

## 五、部署和更新

### 首次部署

```bash
cd /opt/fastapi-demo
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml logs -f app   # 看启动日志
```

### 更新到新版本(拉新镜像后重启)

```bash
# 手动更新:改 .env 里的 IMAGE_TAG 后
docker compose -f docker-compose.prod.yml pull app
docker compose -f docker-compose.prod.yml up -d app

# 或由 GitLab CI 打 tag 时自动执行(见 .gitlab-ci.yml 的 deploy-prod)
```

### 数据库迁移(首次或加新表时)

```bash
# 进 app 容器跑 alembic 迁移
docker compose -f docker-compose.prod.yml exec app \
    alembic upgrade head
```

---

## 六、AI Agent 框架的生产配置要点

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

  external_memory:
    enabled: false
```

> 持久记忆的 `agent_memories` 表依赖 pgvector 扩展,需在独立 PG 上执行:
> `CREATE EXTENSION IF NOT EXISTS vector;`
> 默认迁移(env.py 的 include_object)只在 persistent_memory.datasource == 目标库时建该表。

---

## 七、常用运维命令

```bash
# 查看服务状态
docker compose -f docker-compose.prod.yml ps

# 查看日志
docker compose -f docker-compose.prod.yml logs -f app

# 重启 app
docker compose -f docker-compose.prod.yml restart app

# 进容器排查
docker compose -f docker-compose.prod.yml exec app bash

# 停止全部
docker compose -f docker-compose.prod.yml down

# 停止并删除数据卷(谨慎!会丢数据)
docker compose -f docker-compose.prod.yml down -v
```

---

## 八、手动构建测试(不经过 CI)

```bash
# 公网环境(本地验证 Dockerfile)
docker build -t fastapi-demo:test .

# 模拟公司内网(传 build args)
docker build \
  --build-arg PYTHON_BASE_IMAGE=harbor.example.com/library/python:3.11-slim \
  --build-arg UV_INDEX_URL=https://pypi.internal.example.com/simple \
  --build-arg UV_INSTALLER_URL=https://files.internal.example.com/uv/install.sh \
  -t harbor.example.com/your-group/fastapi-demo:test .

# 手动推送
docker login harbor.example.com
docker push harbor.example.com/your-group/fastapi-demo:test
```
