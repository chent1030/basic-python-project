# CPS 全链路部署与测试

## 组件

```text
旧 CPS mobile -> 旧 CPS Spring Boot -> Agent Framework
                                      -> SigLIP2 embedding
                                      -> SQLite vector service
                                      -> Qwen
```

`/Users/csai/project/models/siglip2-so400m-patch14-384` 作为模型目录挂载到 embedding 容器。当前模型服务默认使用 CPU；有 NVIDIA GPU 时可把 `EMBEDDING_DEVICE=cuda`，并按服务器 CUDA 基础镜像调整 embedding Dockerfile。

## 本机启动

先编辑 `config/agents.docker.yaml`，填入 Qwen Key：

```yaml
providers:
  qwen:
    api_key: "你的 Qwen Key"
```

启动 Agent、模型和向量服务：

```bash
cd /Users/csai/project/basic-project
docker compose -f docker-compose.cps.yml build
docker compose -f docker-compose.cps.yml up -d
```

检查：

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:8008/health
```

旧 CPS 后端的 `application.yml` 在本机运行时使用：

```yaml
cps:
  agent-framework:
    enabled: true
    base-url: http://127.0.0.1:8000/api/v1
  ai:
    embedding:
      base-url: http://127.0.0.1:8090
      endpoint: /image-embeddings
      dimension: 1152
  milvus:
    enabled: true
    base-url: http://127.0.0.1:8008
```

启动旧 CPS：

```bash
cd /Users/csai/project/cps/backend
mvn clean spring-boot:run
```

启动 mobile：

```bash
cd /Users/csai/project/cps/mobile
pnpm dev:h5
```

## 全链路验证

1. 进入 mobile，上传一张 PNG/JPEG/WEBP 图片。
2. 确认 `/api/cps/attachments` 返回附件 ID。
3. 确认 `/api/cps/ai/match-knowledge` 能返回向量模型名和候选案例。
4. 提交问题，确认旧 CPS `cps_issue.agent_inspection_id` 已生成。
5. 在 Agent 前端打开对应巡检，主 Agent 会等待人工确认下一步。
6. 在人工确认页面选择继续、改派、跳过或手工结果。
7. 回到 mobile 完成反馈、整改、上传整改图片和审核关闭。
8. 在 Agent 前端的记忆页面审核旁路 Agent 产生的记忆候选。

## Kubernetes

把 `docker-compose.cps.yml` 中的三个服务分别转换成 Deployment 和 Service。Agent 的 `CPS_CONFIG` 使用 `cps.docker.yaml`，其中 embedding 地址保持：

```yaml
embeddings:
  url: http://cps-embedding:8090/image-embeddings
```

旧 CPS 使用：

```yaml
cps:
  agent-framework:
    base-url: http://cps-agent.cps.svc.cluster.local:8000/api/v1
  ai:
    embedding:
      base-url: http://cps-embedding.cps.svc.cluster.local:8090
  milvus:
    base-url: http://cps-vector.cps.svc.cluster.local:8008
```

模型目录应使用 PersistentVolume 或节点本地只读挂载；不要把 4.3 GB 模型复制进应用镜像。
