# 波次 5 Python 侧设计 — F1 语音 ASR 接入 + F2 三字段语音输入（契约 C-06 speech-to-text）

- 基线：`23f04af`（main，波次 4 交付摘要提交）
- 依据：PRD §20.2 / FR-15 / AC-03；决策记录 `docs/决策记录-20260926.md` D-03；契约 `docs/后端架构与技术设计考虑.md` C-06
- 交付：`app/projects/speech/`（对齐 room_checks 波次 4 结构）+ `app/api/v1/endpoints/speech.py` + 配置节 + 测试

---

## 1. 端点（C-06）

```
POST /api/v1/agent/speech-to-text        （Java CpsAgentFrameworkClient，内网 cps_admin）
```

- **鉴权**：`Identity.require("cps_admin")`（与 C-01/C-03/C-04/C-07 同源，FrameworkRoute）。
- **同步语义**：现场等待；业务结果（含 SKIPPED）200；技术失败 502（不缓存，同幂等键可重试）——与 C-04 完全同构。

### 请求（payload dict 手工 coerce，room_checks 同模式）

| 字段 | 必填 | 说明 |
|---|---|---|
| `submission_id` | ✓ | ≤64 字符 |
| `field` | ✓ | D-03 白名单三字段：`reason` / `short_term` / `long_term`（F2） |
| `attempt` | ✓ | ≥1，同一字段重录递增 |
| `audio_object_key` | 二选一 | RustFS 对象键 ≤512（移动端先上传；Python 内网取流，复用 `CPS_STORAGE_*` env / `cps_agent.initial_review.rustfs` 配置，语音与照片同桶同鉴权） |
| `audio_base64` | 二选一 | 内嵌 base64（联调/小音频直传），粗上限 2×10⁷ 字符防滥用，精确大小解码后校验 |
| `audio_format` | 可选 | 缺省从 `audio_object_key` 后缀推断，再缺省 `wav` |
| `idempotency_key` | 可选 | 键惯例 `speech-{submissionId}-{field}-{attempt}`；契约别名 `request_id` 同样接受（idempotency_key 优先），缺省服务端派生 |

### 响应

```json
{
  "idempotency_key": "speech-sub-1-reason-1",
  "request_id": "speech-sub-1-reason-1",
  "submission_id": "sub-1", "field": "reason", "attempt": 1,
  "status": "TRANSCRIBED | SKIPPED",
  "text": "泵房地面有积水，需要清理",
  "confidence": null,
  "duration_seconds": 8.0, "language": "zh", "model": "qwen3-asr-flash",
  "audio_object_key": null,
  "metadata": {"model_version": "speech/asr-openai-compat@1", "request_id": "asr-1"},
  "transcribed_at": "2026-09-26T08:00:00+00:00",
  "replayed": false
}
```

SKIPPED 时 `text=""` 且 `metadata.skip_reason` 说明原因（不伪造转写文本）。

## 2. 语义边界（PRD §20.2 / AC-03 / D-03）

- **转写结果仅回填表单**（录音 → 转写 → 用户编辑确认 → 提交），**不作点检证据**；端点语义即表单辅助，响应不携带"证据"类标记。
- **敏感词/长度校验不管**：属 A5 文本规则，文本回写 Java 表单后走既有初审管线自然触发。
- **F2 白名单**：`field` 仅接受三字段，非白名单 422（服务层与端点双重校验），不做通用转写入口。

## 3. ASR 选型（同步端点 + 内网 RustFS 取流的约束下）

| 候选 | 结论 | 理由 |
|---|---|---|
| **qwen3-asr-flash（OpenAI 兼容）** | **采用（默认）** | chat/completions `input_audio` 同步调用，`choices[0].message.content` 即转写；音频 ≤10MB/≤5min 与表单语音备注匹配；走 NewAPI/兼容网关同现有 LLM 通道 |
| paraformer-v2 / qwen3-asr-flash-filetrans | 弃用 | 异步任务（X-DashScope-Async + **公网** file_url + 轮询）——内网音频需公网可达 URL，不适配 |
| qwen-audio-3.0-asr-flash | 备选（可配） | 同步但响应结构特殊（output.output.sentence.text），配置 `model` 即切 |

实现：`OpenAiCompatibleAsrClient`（httpx 直连，零新增依赖；不经 `app.services.llm` 单例——需要完整 JSON 响应取 usage/model/id 元数据）。语言参数：`language` 配置非空才发 `asr_options.language`（最小参数集，兼容 NewAPI 网关——与 LLMProviderConfig 注释口径一致）；默认空=自动检测。

## 4. 配置（config 新节，中文注释）

`config/config.yaml` → `cps_agent.speech`（`app/core/config.py` `SpeechConfig` / `infrastructure/config.py` `load_speech_settings()`）：

```yaml
speech:
  enabled: true            # false → SKIPPED
  provider: qwen           # 引用 llm.providers 的 key（base_url/api_key 同源）
  model: "qwen3-asr-flash"
  base_url: ""             # 可选覆盖（ASR 专用网关）
  api_key: ""              # 可选覆盖；支持 enc: 加密（load_settings 全树解密）
  language: ""             # 空=自动检测
  timeout_seconds: 60.0    # 单次转写预算（asyncio.timeout）
  max_audio_bytes: 10485760
```

## 5. 状态与错误分类（对齐 A7/B6「不伪造」）

| 情形 | 结果 | 缓存 |
|---|---|---|
| 正常转写 | `TRANSCRIBED` 200 | 是（LRU 1024 + TTL 24h，命中 `replayed=true`） |
| ASR 未配置/`enabled=false`/api_key 缺失 | `SKIPPED` 200（`skip_reason`） | 是（可重放） |
| ASR 鉴权拒绝（401/403） | `SKIPPED` 200 | 是 |
| RustFS 配置缺失（object_key 路径） | `SKIPPED` 200 | 是 |
| ASR 网络/网关 5xx/超时/响应无效 | 502 `SPEECH_ASR_FAILED` | 否（同键重试） |
| RustFS 取流失败（S3 5xx 等） | 502 `SPEECH_ASR_FAILED` | 否 |
| field 非白名单/音频二选一违约/base64 无效/超 10MB/attempt<1 | 422 | — |

幂等缓存与 room_checks 同实现（进程内 `OrderedDict` + `threading.Lock`，TTL 24h、LRU 1024）。SKIPPED 也缓存：Java 重试同键拿到稳定跳过结果，不重复探测配置。

## 6. 代码结构（对齐 room_checks）

```
app/projects/speech/
  domain/models.py            # 三字段白名单/状态/请求结果模型/audio MIME 表
  application/service.py      # SpeechToTextService：校验→幂等→取音频→ASR→分类
  infrastructure/config.py    # SpeechSettings + load_speech_settings()
  infrastructure/asr_client.py# OpenAiCompatibleAsrClient/FakeAsrClient（httpx）
app/api/v1/endpoints/speech.py  # C-06 端点（coerce/鉴权/422/502 映射）
tests/speech/                 # test_asr_client / test_service / test_endpoint（35 例）
```

音频取流复用 initial_review 的 `RustFSObjectFetcher`（S3 SigV4 GET，CPS_STORAGE_* env 优先），仅实现依赖 `AudioFetcher` Protocol（`fetch_bytes`）。

## 7. 测试与验证

- `tests/speech/` 35 例：ASR 客户端契约（请求形状/语言参数/401≠502/结构无效）、服务层（白名单/二选一/幂等重放/SKIPPED 三源/502 不缓存/object_key 路径/TTL）、端点（契约形状/request_id 别名/格式推断/403 鉴权）。
- 全量 pytest：**417 passed, 2 failed（既有基线失败，非本次引入）, 1 skipped**——较基线 414p/2f 净增 3 例。
- ruff：新增/改动文件零违规。

## 8. 待确认项（不阻塞本次交付）

1. **`asr_options` 网关兼容性**：NewAPI 转发非标顶层字段 `asr_options` 的行为需在联调环境验证；若剥离则 `language` 配置失效（自动检测兜底，功能不致错）。OpenAI 官方 SDK 路径为 `extra_body`，httpx 直发无此问题。
2. **原始音频保留周期**（§27.3 待确认）：`speech/{date}/{request_id}` 对象生命周期——Python 侧只读不删，策略归 Java/存储侧。
3. **speech_transcript 落表**（§2.3 可选）：本波次以幂等缓存 + 结构化日志满足排障；若 Java 侧需要持久转写记录再按表结构补落库。
4. **置信度**：qwen3-asr-flash OpenAI 兼容口不返回置信度 → `confidence=null`（不伪造）；provider 返回则透传。
