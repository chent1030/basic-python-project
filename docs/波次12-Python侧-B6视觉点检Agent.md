# 波次 12 Python 侧 — B6 视觉点检 Agent（room-checks/judge 三级判定 + qwen-vl-plus 集成）

> 基线：basic-project `c69604e`（main）。紧接波次 11（FR-11/12 调度记忆 + 效果评估）。
> 对应 PRD §23.2 拍图判断 / §30.1 辅房点检拍图判断；架构文档契约 B6-ROOM-CHECKS。

## 1. 背景与目标

波次 4 已落地 B6 视觉点检的两阶段契约（type-match → content，状态机
`JUDGED|TYPE_MISMATCH|UNJUDGEABLE|SKIPPED`，端点 `POST /api/v1/agent/room-checks/judge`）。
本波次在其基础上扩展**三级判定算法**，引入 evidence 评分维度，
将整体结论收敛到 `PASS|PARTIAL|PROBLEM` + 0-100 整数 score，并
对接 Java 端回调（PRD §30.1 闭环）。

两条路径并存：
- **C-04（波次 4 既有）**：Java 同步提交 → 立即返回，状态机式契约；
  继续服务于老链路（`/api/v1/agent/room-checks/judge`）。
- **B6（本波次新增）**：异步批量拍照复核，三级判定 + score 量化；
  端点 `POST /api/v1/agent/room-checks/v2/judge` + `/v2/rejudge`。

## 2. 架构决策 — 为什么 type-match → content → evidence 三级而非单次调用

### 2.1 设计动机

- **抗模型方差**：单次调用要求 qwen-vl-plus 同时关注「照片主体类型」+
  「关键物证是否匹配」+「证据强度/结论措辞」三件事，模型在长 prompt
  下出现遗漏、跑题或 JSON 字段错配的概率显著增加；三级拆分后每一
  级 prompt 责任单一，便于单级回退/重试/审计。
- **审计可回放**：每一级 raw_output 都进入 `RoomCheckVerdict.raw_output`
  （`{"type_match": ..., "content": ..., "evidence": ...}`），合规抽查
  时可直接定位「模型在哪一级、哪个具体事实上答错了」。
- **降级路径明确**：任何一级失败/超时 → 固定 fallback（`PROBLEM/0` +
  `reasons=("视觉判定超时/失败，建议人工复检",)`），Java 端契约无需
  多分支处理；type-match 解析失败被视为「照片主体不符」（业务上的
  合理退化），不进入 fallback。

### 2.2 与波次 4 两阶段的差异

| 维度 | 波次 4 两阶段（C-04） | 波次 12 三级（B6） |
|---|---|---|
| 端点 | `/agent/room-checks/judge` | `/agent/room-checks/v2/judge` |
| 状态机 | `JUDGED/TYPE_MISMATCH/UNJUDGEABLE/SKIPPED` | `PASS/PARTIAL/PROBLEM` + score |
| 调用次数 | 1-2 次模型调用 | 1-3 次模型调用（每级一次） |
| 输出形态 | 业务字段（verdict/evidence/photo_subject） | 评分（0-100）+ 扣分明细 |
| Java 集成 | 同步提交即返回 | judge 后异步回调 `POST /api/cps/room-checks/{id}/callback` |
| 幂等键 | `room-judge-{submissionId}-{itemId}-{attempt}` | fingerprint 直接做键（30min LRU+TTL） |
| 角色 | `cps_admin` | judge: `cps_admin`；rejudge: `cps_admin` + `cps_supervisor` |

## 3. 三级判定算法与评分公式

### 3.1 流程图

```
┌──────────────────────────────────────────────────────────────────┐
│ Level 1: type-match                                                │
│   - 抽取照片元数据（EXIF/filename/upload tag）                      │
│   - qwen-vl-plus 判断 observed_type vs expected_match_type          │
│   - 不匹配 → 直接 PROBLEM/0（不进入 Level 2/3）                     │
└──────────────────────────────────────────────────────────────────┘
                              │ matched=true
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Level 2: content                                                   │
│   - 检查关键物证（expected_keywords）是否在照片中可见               │
│   - 输出 content_match=true|false + matched_keywords/missing_keywords │
└──────────────────────────────────────────────────────────────────┘
                              │ content_match=true
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│ Level 3: evidence                                                  │
│   - 评估证据强度（STRONG/MODERATE/WEAK/NONE）                       │
│   - 提取 visual_features（用于 Java 端 audit log）                  │
└──────────────────────────────────────────────────────────────────┘
                              │
                              ▼
                    score = 100 - deductions
                    overall = score>=80 ? PASS : (score>=50 ? PARTIAL : PROBLEM)
```

### 3.2 扣分明细（score 起始 100）

| 维度 | 扣分 | 说明 |
|---|---|---|
| content 不匹配 | **-40** | Level 2 输出 `content_match=false`；说明关键物证缺失/被遮挡 |
| keywords 缺失（content 仍 match） | **-20** | `expected_keywords` 有但未在照片中识别 |
| evidence 模糊 | **-30** | evidence_strength ∈ {WEAK, NONE}；说明照片不清晰或视角不佳 |
| 其他（兜底） | **-10** | 预留维度（如 evidence MODERATE 轻度扣分） |

### 3.3 边界示例

- `100 - 20 (kw缺1) = 80` → **PASS**（边界）
- `100 - 20 (kw缺1) - 10 (other) = 70` → **PARTIAL**
- `100 - 40 (content_fail) = 60` → **PARTIAL**
- `100 - 40 - 30 = 30` → **PROBLEM**
- type-match 不匹配或解析失败 → 直接 `0/PROBLEM`

### 3.4 Fallback 语义

任何一级抛 `ModelUnavailableError` / `ModelCallError` / `asyncio.TimeoutError` →
固定返回：
```python
RoomCheckVerdict(
    overall=JudgeVerdict.PROBLEM,
    score=0,
    reasons=("视觉判定超时/失败，建议人工复检",),
    model_name="qwen-vl-plus",
)
```
type-match 解析失败视为「主体不符」（业务合理退化），不进 fallback。

## 4. 与 PRD §30.1 辅房点检拍图判断接续

§30.1 拍图判断字段语义：
- 输入：`photo_object_key`（RustFS object key）+ `expected_match_type` + `expected_keywords`
- 输出：`overall` + `score` + `reasons` + 审计 raw_output

本波次 Python 侧：
- 入参 `photoObjectKey` / `photoUrl`（互斥，至少有其一）
- 出参字段 `fingerprint / overall / score / reasons / judged_at / model_name / raw_output`
- `raw_output` 含 `{"type_match": {...}, "content": {...}, "evidence": {...}}` 三级原始输出

## 5. 与 Java 端回调集成点

### 5.1 Java 端契约（待 Java 侧实现）

```
POST /api/cps/room-checks/{fingerprint}/callback
Content-Type: application/json
Body: {
  "fingerprint": str,
  "overall": "PASS"|"PARTIAL"|"PROBLEM",
  "score": int (0-100),
  "reasons": list[str]
}
```

### 5.2 Python 端实现

`JavaRoomCheckCallbackClient`（`app/projects/room_checks/infrastructure/callback_client.py`）：
- httpx AsyncClient，每请求新建（提交频率低不值得连接池）
- 30s timeout + 最多 2 次重试（共 3 次尝试）+ 退避 (1.0s, 3.0s)
- fingerprint URL 模板注入防御（拒绝含 `/`、`{`、`}` 的 fingerprint）
- 失败 → 写 WARNING 日志（便于运维排查），不抛异常阻塞调用方

`RoomCheckB6JudgeService.judge()` 在 verdict 生成后通过 `asyncio.create_task`
调度 callback（fire-and-forget），并把 task 句柄记入 `_pending_callbacks`
（与 `initial_review` 同模式，便于测试显式 await）。

## 6. 幂等设计

进程内 LRU + TTL（`CACHE_TTL_SECONDS=1800, CACHE_MAX_ENTRIES=1024`）：
- 30 分钟内同 fingerprint 直接返回缓存（`replayed=true` 标记，由调用方
  自行观察 `_pending_callbacks` 或 `cache.peek`）
- 多实例部署演进：Redis / DB（同 inspection_plans 模式，待联调期评估）

## 7. 文件清单

```
basic-project/
├── app/projects/room_checks/
│   ├── domain/
│   │   ├── evidence_models.py          # B6 领域模型（RoomType/JudgeVerdict/RoomCheckEvidence/RoomCheckVerdict）
│   │   └── models.py                    # 既有 C-04 模型（不动）
│   ├── infrastructure/
│   │   ├── callback_client.py           # B6 Java 回调客户端
│   │   ├── prompt_builder.py            # B6 三级 PromptBuilder
│   │   └── vision_client.py             # B6 qwen-vl-plus 多模态客户端
│   ├── application/
│   │   └── judge_service.py             # B6 RoomCheckB6JudgeService
│   └── api/
│       ├── __init__.py
│       └── router.py                    # B6 v2/judge + v2/rejudge 端点
└── tests/projects/
    ├── conftest.py
    ├── test_room_checks_judge.py        # 17 tests
    ├── test_room_checks_vision.py       # 9 tests
    ├── test_room_checks_router.py       # 14 tests
    └── test_room_checks_callback.py     # 12 tests
```

## 8. 测试覆盖

| 文件 | tests | 覆盖点 |
|---|---|---|
| `test_room_checks_judge.py` | 17 | type-match 第一级拒绝；content 第二级正确；evidence 第三级扣分正确；PASS/PARTIAL/PROBLEM 边界；解析失败 fallback；幂等 LRU；rejudge 清缓存重跑；空 fingerprint/缺照片 raise JudgeRequestError |
| `test_room_checks_vision.py` | 9 | FakeVisionClient round-trip；prompt 注入 expected_keywords/match_type；3 级 prompt 版本锚点；ModelUnavailable 传播 |
| `test_room_checks_router.py` | 14 | /judge round-trip；401/403/422（缺 fingerprint + 非法 roomType + 双 photo 缺失）；/rejudge 双角色权限；cache_cleared 行为 |
| `test_room_checks_callback.py` | 12 | Java client round-trip；HTTP 5xx + ConnectError 重试；URL 注入防御；fake dispatch 脚本消费；service → callback 闭环 |

合计 52 tests（基线 634 → 686 passed）。

## 9. TODO（联调期）

1. **真实 qwen-vl-plus 真实图片联调**：当前所有测试基于 FakeVisionModelClient
   + MockTransport；上线前需在测试环境跑真实图（地面/工器具/电缆夹层各 1 张），
   验证 prompt 边界条件（Chinese keyword 编码、长尾照片类别）。
2. **持久化 callback 重试队列**：进程内 `asyncio.create_task` 在服务重启
   时丢失；待接入 Redis Streams 或 DB 持久队列（联调清单之一）。
3. **多实例 cache 共享**：Redis 实现（与 inspection_plans 演进路线同步）。
4. **真实 RustFS 拉图**：当前 `photo_data_url` 写死为测试 stub；
   联调期接入 RustFSObjectFetcher（与 initial_review 同模式）。
5. **Java 端契约联调**：端点 `/api/cps/room-checks/{id}/callback` 由 Java
   端提供（未在本计划实现范围内）；body 字段对齐后做联调。

## 10. 关键决策记录

1. **路径用 `/v2/judge` 而非 `/judge`**：与波次 4 C-04 端点共存，
   避免破坏既有 Java 集成；显式标注语义版本（v2 = B6 三级判定）。
2. **rejudge 双角色限定**：cps_admin + cps_supervisor 同时具备（手写
   双角色校验，因 `Principal.require()` 仅做交集）；降低误用风险。
3. **photo_object_key / photo_url 互斥**：与既有 C-04 端点同语义；
   两者皆空时返回 422 而非通过。
4. **score 起始 100 而非 0**：便于审计「满分满分扣分明细」对应 Java
   deduction 字段语义；`100 - 20 = 80` PASS 边界符合运维直觉。
5. **fallback 仅在 type-match 通过后才进入**：避免网络瞬抖时一刀切
   fallback（增加人工复核负担），但 type-match 失败 → 直接 PROBLEM/0
   （业务明确，不需要再调模型）。