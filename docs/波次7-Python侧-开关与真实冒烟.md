# 波次 7 Python 侧：独立模型开关 + 真实模型冒烟

> 基线：`b205c82`（main）。范围：仅 basic-project 仓。
> 对应联调清单：⑥独立模型开关、⑦真实模型冒烟 + ASR；另含 Java 联调观察项两项修复。

---

## 1. 背景与问题

- **清单⑥**：`room_checks`（C-04 点检视觉判定）与 `speech`（C-06 ASR）此前分别与
  `initial_review.model_check.enabled`（C-01）和 `speech.enabled` 共用一个开关，
  只能同开同关，无法单独关闭某一条真实模型链路（联调时只想开 C-04 不想开 C-01
  做不到）。
- **清单⑦**：波次 2 的真实冒烟只覆盖 A7/A8 链路，C-04（视觉判定）、C-06（ASR）、
  C-01（初审文本检查）三条线从未用真实模型端到端冒烟过，配置错误
  （如模型名写错）只有在联调时才暴露。
- **观察项**（波次 6 遗留）：`run_once` 返回无 `run_no`（Java 首跑判定要再查列表）；
  C-01 `replayed=True` 语义在 Java 侧被误读为首建判据。

## 2. 任务 1：独立模型开关（清单⑥）

### 2.1 设计：三级回退链

新增两个独立开关节点与一个独立 env，**旧开关全部保留且继续生效**：

| 链路 | 新 yaml 开关 | 新 env | 回退链（自高到低） | 全未配默认 |
|---|---|---|---|---|
| C-04 视觉判定 | `cps_agent.room_checks.vision.enabled` | `CPS_ROOM_CHECKS_VISION_ENABLED` | 新 env → 新 yaml → 旧 env `CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED` → 旧 yaml `initial_review.model_check.enabled` | **false**（SKIPPED 不伪造） |
| C-06 ASR | `cps_agent.speech.asr.enabled` | `CPS_SPEECH_ASR_ENABLED` | 新 env → 新 yaml → 旧 yaml `speech.enabled` | **false** |
| C-01 初审 | `initial_review.model_check.enabled`（不变，波次 7 起仅控 C-01） | `CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED`（不变） | env → yaml；**yaml 未配默认 true**（既有契约） | true |

回退链顺序的理由（关键决策）：**新开关全域优先于旧开关，同级 env > yaml**。
场景：旧部署已设 `CPS_INITIAL_REVIEW_MODEL_CHECK_ENABLED=false`（原来一并关掉
C-01+C-04），现在只想单独恢复 C-04 → 必须允许新 yaml `room_checks.vision.enabled=true`
赢过旧 env。若旧 env 优先，该部署就无法单独开 C-04。

### 2.2 实现

- `app/core/config.py`：`ModelCheckConfig.enabled` 改 `bool | None`（None=未配置）；
  新增 `RoomChecksConfig`/`RoomChecksVisionConfig`、`AsrSwitchConfig`；
  `SpeechConfig` 增 `asr` 字段（`enabled` 保留为旧开关）；
  `CpsAgentConfig` 增 `room_checks` 字段。
- 新建 `app/projects/room_checks/infrastructure/config.py`：
  `resolve_vision_enabled(env) -> tuple[bool, str]`（返回决策来源说明，进 SKIPPED reason）。
- `app/projects/speech/infrastructure/config.py`：`resolve_asr_enabled(env)` 同款；
  禁用文案列出开关链，运维一眼看出该配哪个。
- `RoomCheckJudgeService.__init__` 增 `vision_enabled: bool | None` 显式注入
  （测试用；None → 走生产回退链），`judge()` 开关判断改用解析结果，
  SKIPPED 消息含来源（如「模型检查已显式禁用（env CPS_ROOM_CHECKS_VISION_ENABLED）」）。
- `config/config.yaml`：新增 `room_checks.vision.enabled`、`speech.asr.enabled`
  （均留空=未配置→回退链），中文注释写明回退链与兼容口径；
  `model_check.enabled` 注释改「波次 7 起仅控制 C-01 初审」。

### 2.3 测试

- 新建 `tests/room_checks/test_config_switch.py`（10 用例）：全未配→False；
  旧 yaml/旧 env 兼容；新 yaml 双向覆盖旧开关；新 env 最高；新 env 赢旧 env；
  ASR 链同款 4 例；C-01 未配默认 True 契约不变。
- `tests/room_checks/test_service.py`：`make_service` 增 `vision_enabled` 参数
  （None 时镜像 `model_check.enabled` 保持旧用例语义）；新增 2 个服务级独立性用例
  （vision off + model_check on → SKIPPED 且零模型调用；vision on + model_check
  off → 正常 JUDGED）。
- 兼容性实测：当前 yaml 只配旧开关（`model_check.enabled=true`、
  `speech.enabled=true`）→ 解析结果 C-04/C-06 均开（回退来源「旧开关」），部署行为不变。

## 3. 任务 2：真实模型冒烟脚本（清单⑦ + ASR）

`scripts/j7_real_model_smoke.py`（手动运行，不进 pytest）：

| 段 | 链路 | 做法 | 真实结果（2025-02 实测，local.yaml qwen key） |
|---|---|---|---|
| C-04 | 视觉判定 | 自造 64×64 渐变 PNG PUT 进 RustFS `smoke/j7/{before,after}.png`（同 key 覆盖=幂等）→ `RoomCheckJudgeService.judge()` 全链真调用，开关走生产回退链 | **TYPE_MISMATCH（真实判定）**：qwen-vl 正确识别合成图不含「地面」，type_match 阶段真实结构化输出 + stage_trace 留痕 |
| C-06 | ASR | 合成 1 秒 440Hz WAV（stdlib wave）→ `OpenAiCompatibleAsrClient.transcribe()` 真调用 | **TRANSCRIBED（真实调用）**：网关/解析链路通；合成音无人声，转写为空属预期（脚本内注明） |
| C-01 | 初审文本检查 | `DashScopeModelClient.complete_text_json()` 真调用（`short_term_measure="已整改"` 一例） | **CHECKED（真实判定）**：语义有效性结构化输出成功（首次输出非 JSON 时自纠重试一次成功） |

输出：stdout 人读摘要 + `scripts/j7_smoke_result.json`（覆盖写；含每段开关决策来源、
上传 etag、stage_trace、模型版本、错误全文）。退出码 0=有真实判定 / 2=全 SKIPPED /
1=有异常。幂等可重跑（固定幂等键 `room-judge-j7-smoke-0001-item-ground-1`、
RustFS 同 key 覆盖、无 DB 落库）。

### 3.1 冒烟发现并修复的真实问题

1. **`vision_model: "Qwen2.5-VL-7B-Instruct"` 在当前 DashScope 账号 404
   model_not_found**（`config/config.yaml:199`，自波次 4 起潜伏——此前测试全用
   Fake，从未真调过视觉模型）。已改为 **`qwen-vl-plus`**（冒烟实测可调；
   `qwen-vl-max` 探测亦通）。`config/agents.yaml` 的同名模型属 agent 框架线，**遗留**。
2. **qwen-vl 系要求图片边长 >10px**：首版 8×8 PNG 被 400
   `InvalidParameter [height:8 or width:8 must be larger than 10]` 拒绝 →
   脚本改 64×64 渐变图（纯色对类型匹配阶段也无纹理可辨）。
3. `llm` 单例必须先 `startup()`：脚本首跑 C-04 段 SKIPPED「未配置任何 LLM
   provider」，实为单例未初始化 → 脚本 main 里显式 `await llm.startup()`。

## 4. 任务 3：观察项

### 4.1 run_once 返回补 run_no

`weekly_report/application/service.py` `run_once()` 的 6 处返回 dict 中，
**有数据库行**的 4 处补 `run_no`：COMPLETED 终态与两处 FAILED 用 `next_run_no`；
同窗口 COMPLETED 幂等跳过用 `latest.run_no`。**无行的 2 处**（advisory-lock 跳过、
UNIQUE 兜底竞态跳过）不补——没有 run_no 可指，编一个就是伪造（诚实口径）。
schema 冻结测试只冻 C-05 列表字段，run_once 返回不冻结；测试补 3 处断言。

### 4.2 C-01 replayed=True 语义澄清

`replayed = not created`：True＝「该幂等键下行**已存在**」（含 RUNNING 在途
重放与并发 UNIQUE 兜底复用），**行存在 ≠ 本请求首建**；严格首建判定看
`replayed=False`，勿用它区分首建/接管（接管场景应以 status==RUNNING + 自身
重试上下文判断）。已写入 `_submit_response` 代码注释与
`docs/波次6-Python侧-A10C7设计.md` §3 C-01 契约行。

## 5. 影响面与遗留

- **影响面**：`app/core/config.py`（新配置节点，全部可选、默认兼容）、
  room_checks/speech/initial_review 配置与服务、weekly_report 返回体（只增键）、
  `config/config.yaml`（vision_model 值修正 + 新开关注释）。
- **兼容性**：只配旧开关的部署行为零变化（回退链实测验证）；C-01 契约不变。
- **遗留**：①`config/agents.yaml:19` 的 `Qwen2.5-VL-7B-Instruct` 同样 404，属
  agent 框架线（本波次不动）；②C-04 若要验证到 JUDGED 终态需真实地面照片
  （当前合成图止步 TYPE_MISMATCH——链路已全通，模型判定诚实）；③ASR 冒烟用
  合成音仅验证链路，识别准确率待真实音频。
