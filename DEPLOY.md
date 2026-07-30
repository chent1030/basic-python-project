# 部署指南 — 服务器源码构建 + Docker Compose 部署

部署方式:在**生产服务器上从源码 `docker build` 构建镜像**再运行(不拉远程应用镜像,也不推送)。
构建时拉基础镜像(python)和 Python 依赖包都走 **JFrog 代理**(公司内网不能直连 Docker Hub / PyPI),所以需要 `docker login` JFrog。

用 `deploy.sh` 一键完成:登录 JFrog → 构建镜像 → 数据库迁移 → 启动服务。

---

## 一、准备服务器(一次性)

```bash
# 安装 docker + compose 插件
curl -fsSL https://get.docker.com | sh

# 拉取代码
git clone <仓库地址> /opt/fastapi-demo
cd /opt/fastapi-demo
```

## 二、配置 .env 和 config/local.yaml

```bash
cp .env.example .env
vi .env                # 填 JFrog 地址/账号/密码、构建参数、数据库密码
```

`.env` 关键配置(构建相关):
```ini
# JFrog 登录(让 docker build 能拉基础镜像)
REGISTRY=your-jfrog.company.com          # 不带协议
REGISTRY_USER=用户名
REGISTRY_PASSWORD=密码

# 构建参数:基础镜像 + Python 包走 JFrog
PYTHON_BASE_IMAGE=your-jfrog.company.com/docker-proxy/python:3.11-slim
UV_INDEX_URL=https://your-jfrog.company.com/api/pypi/pypi-proxy/simple
UV_INSTALLER_URL=https://your-jfrog.company.com/files/uv/install.sh   # 内网拉 uv 脚本(可选)

# 数据库密码(与 config/local.yaml 一致)
POSTGRES_PASSWORD=数据库密码
```

```bash
vi config/local.yaml    # 填 LLM provider(qwen key)、数据源密码
                        # host 用服务名:postgres / redis
```

## 三、一键部署

```bash
./deploy.sh deploy
```

`deploy` 自动完成:登录 JFrog → 构建镜像(基础镜像/Python 包走 JFrog)→ 启动 postgres/redis → 数据库迁移 → 启动 app → 清理旧镜像。

## 四、deploy.sh 子命令

| 命令 | 作用 |
|------|------|
| `./deploy.sh deploy` | **完整部署**(login + build + migrate + up),最常用 |
| `./deploy.sh build` | 只登录 + 构建镜像(不启动) |
| `./deploy.sh up` | 只启动/更新服务(不构建) |
| `./deploy.sh migrate` | 只跑数据库迁移 |
| `./deploy.sh restart` | 重启 app 容器 |
| `./deploy.sh stop` | 停止所有服务 |
| `./deploy.sh logs` | 跟踪 app 日志 |
| `./deploy.sh status` | 查看服务状态 |
| `./deploy.sh clean` | 停止并删除容器(保留数据卷) |

## 五、更新代码后重新部署

```bash
cd /opt/fastapi-demo
git pull                # 拉最新代码
./deploy.sh deploy      # 重新构建 + 部署
```

代码改了就 `git pull` + `./deploy.sh deploy`,会重新 build 镜像并重启。

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
```

> 持久记忆的 `agent_memories` 表依赖 pgvector 扩展,需在独立 PG 执行 `CREATE EXTENSION IF NOT EXISTS vector;`

## 七、常见问题

**Q: `docker build` 时拉基础镜像失败?**
确认已 `docker login` 到 JFrog(deploy.sh 自动做),且 `.env` 的 `PYTHON_BASE_IMAGE` 是 JFrog 里存在的 python 镜像地址。

**Q: uv 装 Python 包失败?**
确认 `.env` 的 `UV_INDEX_URL` 指向 JFrog 的 PyPI 仓库,且地址能访问。带认证的源填 `UV_INDEX_USER`/`UV_INDEX_PASSWORD`。

**Q: 怎么验证部署成功?**
```bash
./deploy.sh status    # 服务状态
./deploy.sh logs      # 看 "All datasources, LLM, agent gateway ... ready"
curl http://localhost:8000/health
```
