# CPS 前端工作台

独立的 React + TypeScript + Tailwind CSS 项目，使用 Vite 构建，接入根项目的 `/api/v1/cps` 后端。
不恢复旧独立 CPS 项目，不包含浏览器端模型调用，也不提供 Agent 或模型的可视化配置器。

## 1. 本地运行

环境：Node.js 22.12+ 或兼容的较新 LTS 版本、npm。已在 Node.js 24 上验证。

```bash
cd /Users/csai/project/basic-project/cps-frontend
npm ci
cp .env.example .env.local
npm run dev
```

打开 `http://127.0.0.1:5173`。默认代理配置：

```dotenv
VITE_API_BASE_URL=/api/v1
CPS_PROXY_TARGET=http://127.0.0.1:8000
```

`VITE_API_BASE_URL` 是进入浏览器构建产物的公开配置，不得填写 API 密钥或令牌。
`CPS_PROXY_TARGET` 仅用于 Vite 开发代理，不是模型提供商地址。
工程师继续在根目录 `config/agents.yaml` 配置每个 Agent 的模型、提供商、`base_url` 与 `api_key`。

从根目录另开终端启动现有后端：

```bash
uv sync --locked --extra dev
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000
```

根服务仍要求按主项目 README 配置其数据库及公共基础设施。
CPS 的 Agent Worker 必须启用，或者另行启动独立 Worker；仅运行前端不会执行模型任务。
参考 `../docs/CPS后端迁移与接口指南.md`。

## 2. 身份连接

当前后端未提供生产级 CPS 用户登录接口。前端提供访问令牌连接入口，不伪造账号密码登录，
不使用根项目那个缺少 CPS 身份声明的示例 `/auth/token`。

生产令牌应由可信认证系统签发，并通过后端验签，包含：

| 声明 | 含义 |
|---|---|
| `type` | 必须为 `access` |
| `sub` | 用户标识 |
| `tenant_id` | 租户标识 |
| `roles` | CPS 角色列表 |
| `exp` | 有效期 |

**仅限本地开发**，可使用根项目已有签名函数生成测试身份；不要将签名密钥、测试管理员令牌发布到前端：

```bash
cd /Users/csai/project/basic-project
uv run --locked python - <<'PY'
from app.core.security import create_access_token

print(create_access_token({
    "sub": "local-cps-admin",
    "tenant_id": "local-factory",
    "roles": ["cps_admin"],
}))
PY
```

令牌仅保存在当前页面内存中，不使用 localStorage / sessionStorage，不进入 URL。
连接前先读取巡检接口，由后端核验身份。刷新页面、主动断开或到期后需重新连接。
断开连接会取消读请求并清除查询缓存，避免不同身份之间复用数据。
前端解析声明只用于显示和隐藏操作按钮，授权和租户隔离始终由后端实现。

## 3. 页面与业务能力

| 页面 | 能力 |
|---|---|
| 工作台 | 真实巡检数量、最近巡检、人工确认入口、业务链路说明 |
| 巡检任务 | 搜索与状态过滤、新建任务、补充目标和现场信息 |
| 巡检详情 | 启动、上传证据、退回/取消、人工结果、版本与当前执行节点 |
| 人工确认 | 主 Agent 调度确认，以及独立的专家输出审核 |
| 报告与成果 | 查看/编辑待审结构化结果、沙箱报告预览、确认后归档与发布计划 |
| 运行监控 | 当前运行状态、Token、错误、任务审计与完整分页链路事件 |
| 计划任务 | 跟进已发布任务、查看依赖/验收条件、更新状态与说明 |
| 报告档案 | 查询已归档巡检内容 |
| 经验记忆 | 候选审核、范围/内容修订、停用、启用、有效期与历史版本回滚 |
| Agent 目录 | 八类已注册 Agent 的职责、审核角色和实际输出 JSON Schema，只读 |
| 统计与效果 | 后端统计、人工改派率、结果修改率与记忆效果分组 |

调度操作支持：批准、改派、跳过、重试、退回、转人工。改派必须选择已注册的可调度专家；
操作理由与补充指令发往后端审计链路，后续由观察员提取为候选记忆，不能在前端直接把改派激活为长期记忆。

专家结果审核与下一步调用审核相互独立。报告、计划仍需确认后执行归档/发布。
人工结果和修改后的专家结果使用完整 JSON 编辑器，并展示输出契约，由后端进行领域校验；
前端没有重新实现或放宽后端的证据引用、统计口径、任务依赖和结果失效规则。

### 数据刷新与异常

- 不携带演示数据；未连接时显示连接引导，空数据和接口错误分别展示。
- 巡检详情和事件每 3 秒读取，确认中心每 5 秒读取，其他列表每 10 秒读取；隐藏标签页停止间隔刷新。
- 监控使用带 Bearer 的轮询和事件游标分页，不以不支持认证头的原生 EventSource 绕过认证。
- 列表按 200 条拉取至完整，事件按后端游标读取至完整，不把第一页当全量。
- 写请求携带后端要求的乐观锁版本和幂等键；不自动重试写操作。同一表单相同内容重试时复用幂等键，避免响应丢失后重复创建任务。
- 审批表单固定打开时的任务版本。发生 `409` 时展示冲突，不自动使用新版本继续批准。
- 图片通过带认证的请求读取为临时 Blob URL，组件卸载时释放；不在图片地址拼接令牌。
- 报告通过无脚本权限的沙箱 iframe 预览；结构化文本不使用 `dangerouslySetInnerHTML`。

## 4. 代码结构

```text
src/
  domain/           CPS 类型、状态、身份显示与命令辅助
  infrastructure/   HTTP 适配、分页、鉴权头与错误映射
  application/      会话生命周期、查询缓存、命令与刷新
  features/         按业务能力组织的 React 页面和表单
  shared/           无业务依赖的界面组件
  test/             单元测试基础设施
e2e/                浏览器交互测试及测试专用 API fixtures
```

前端按领域 / 应用 / 基础设施 / 展示分层，但不把后端 DDD 领域规则复制成第二套事实来源。
Agent 顺序、并行、HITL 开关和组合定义继续由工程师在后端写代码控制。

## 5. 验证与构建

```bash
npm run typecheck
npm test
npx playwright install chromium
npm run test:e2e
npm run build
npm run preview
```

浏览器测试使用测试文件中显式声明的 API fixtures，验证创建、改派、结果审核、证据上传、
记忆审核、任务更新、错误提示、会话清理、沙箱与移动端布局；这些数据不进入应用运行代码。
它们不等价于真实模型、企业认证系统或生产数据联调。

构建输出为 `dist/`。部署时需要 SPA 路由回退，以及 `/api/` 到后端的同源代理。
Vite 开发代理不会随静态构建自动部署；下面给出 Nginx 配置示例。

```nginx
server {
    listen 80;
    server_name cps.example.internal;
    root /srv/cps-frontend/dist;
    index index.html;
    client_max_body_size 16m;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_buffering off;
    }

    location / {
        try_files $uri $uri/ /index.html;
    }
}
```

生产环境须在网关启用 HTTPS，使用正式认证、最小角色权限及后端访问控制。
前端环境变量只用于公共地址配置，不保存模型密钥或 JWT 签名密钥。
