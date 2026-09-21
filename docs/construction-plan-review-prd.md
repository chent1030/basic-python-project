# 施工方案智能审核 Agent PRD

## 1. 文档信息

| 项目 | 内容 |
| --- | --- |
| 文档名称 | 施工方案智能审核 Agent |
| 文档版本 | v1.0 |
| 当前默认识别模型 | Qwen3.8 |
| 目标实现方式 | DeepAgents + 可切换识别 Provider |
| 审核依据 | `deman.md` 中施工方案部分 |

## 2. 背景与目标

将施工方案文件交给智能体自动完成内容完整性、表格结构、封面信息、人员合规、施工周期和应急预案审核，并输出可追溯的审核结论。

本系统需要支持识别模型或 OCR 工具替换，但不让底层识别工具影响上层审核规则和 Agent 编排。

## 3. 产品范围

### 3.1 本期范围

只审核施工方案书文件，文件来源为请求对象中的：

```text
constructionProgrammeFileInfoList[].s3PreviewFileUrl
```

审核内容包括：

1. 全文错别字提醒，姓名文本不做错别字识别。
2. 十个一级标题及对应二级标题、内容完整性。
3. 施工工具表、人员表、风险评估表、每日工作表、化学品表的表头检查。
4. 封面校对人、批复人、公章存在性、红色校验及印章内容与 `vendorName` 匹配、工程师和经理签字、日期、作业类型勾选。公章红色校验不能只依赖 LLM 输出。
5. 作业人员持证情况和年龄合规性。
6. 施工周期与每日工作记录覆盖情况。
7. 应急救援预案的标题、角色、姓名和联系电话。

### 3.2 明确不在范围内

以下字段可以继续存在于接口模型中，但本系统不读取、不下载、不审核：

```text
constructionTechDiscloseFileInfoList
hotWorkTechDiscloseFileInfoList
```

同时移除：

- 安全技术交底审核
- 动火作业交底审核
- 动火交底文件上传校验
- 技术交底与施工方案之间的一致性校验
- 公章是否位于文件底部的判断

## 4. 用户与使用场景

### 4.1 主要用户

- EHS 审核人员
- 项目管理人员
- 施工供应商管理人员

### 4.2 典型流程

1. 上游系统提交施工申请对象。
2. Agent 从 `constructionProgrammeFileInfoList` 获取施工方案文件。
3. 系统使用 `s3PreviewFileUrl` 下载并处理文件。
4. 系统完成自动审核。
5. 上游系统获取审核状态、问题项、建议项和证据位置。

## 5. 输入与输出

### 5.1 输入

业务上下文来自 `EhsConstruct`，主要包括：

- `vendorName`
- `workDay`
- `processInitiatedAt`
- `operator`
- `workInfo`
- `constructionProgrammeFileInfoList`

文件处理只允许使用 `s3PreviewFileUrl`。删除标记为 `isDelete=true` 的文件不参与审核。

### 5.2 输出

```json
{
  "run_id": "run-20260904-001",
  "status": "REJECT",
  "summary": {
    "reject_count": 2,
    "suggestion_count": 1,
    "reminder_count": 1,
    "review_required_count": 0
  },
  "findings": [],
  "processing": {
    "file_count": 1,
    "asset_count": 12,
    "failed_asset_count": 0
  }
}
```

审核状态定义：

| 状态 | 含义 |
| --- | --- |
| `PASS` | 全部规则通过 |
| `PASS_WITH_SUGGESTIONS` | 无否决项，仅有建议或提醒 |
| `REJECT` | 存在证据充分的否决项 |
| `REVIEW_REQUIRED` | 关键内容无法可靠识别，需要人工确认 |
| `UNAVAILABLE` | 文件无法下载或处理失败 |

## 6. 定位与证据要求

原文件可能没有可用的打印页码，因此系统不依赖页码定位。

文件处理时生成内部视觉资产编号：

```text
asset_id
asset_index
region_id
bbox
```

问题证据至少包含：

```json
{
  "file_name": "施工方案.pdf",
  "asset_id": "file-001-image-007",
  "region_id": "file-001-image-007-region-02",
  "bbox": [120, 340, 1680, 920],
  "observation_id": "obs-001"
}
```

`asset_index` 是系统生成的处理顺序，不代表文档打印页码。

## 7. Agent 与任务编排

### 7.1 Supervisor Agent

负责：

- 创建审核任务和 `run_id`
- 调用文件处理工具
- 调度视觉识别任务
- 等待文档事实生成
- 并行调用规则 Agent
- 触发定向重试
- 汇总最终报告

Supervisor 不直接解析图片，也不在 Agent 消息中传递完整图片或大段文本。

### 7.2 文件处理任务

负责：

- 校验和下载 `s3PreviewFileUrl`
- 文件大小和空文件检查
- 文件 hash 计算和去重
- PDF 或图片转换为视觉资产
- 为每个资产生成 `asset_id`

### 7.3 视觉识别任务

当前默认调用 `qwen3.8`，识别：

- 印刷体文字
- 标题和段落
- 表格及表头
- 复选框状态
- 手写签字
- 日期
- 公章候选区域
- 印章文字，并与输入的 `vendorName` 比对

识别结果必须转换为统一的 `ExtractionResult`，供后续 Agent 使用。Qwen 输出的公章候选区域仅作为颜色校验的输入，不直接作为红色结论。

公章红色校验采用双重结果：

1. Qwen 识别公章候选区域并输出视觉判断。
2. Python 图像处理在候选区域内进行独立颜色分析（例如 HSV/Lab 红色像素比例、阈值和连通区域检查）。
3. 以图像分析结果作为红色判断的主要依据，LLM 结果作为辅助证据。
4. 候选区域缺失、颜色比例处于不确定区间或两种结果冲突时，触发局部重识别；仍无法确定则标记为 `REVIEW_REQUIRED`。

该流程只判断公章是否为红色，不判断公章是否位于文件底部或其他位置。

### 7.4 文档事实汇总任务

将各视觉资产结果合并为 `ConstructionFacts`，作为所有规则 Agent 的唯一事实输入。

### 7.5 规则 Agent

规则 Agent 可以并行运行：

1. 章节结构审核
2. 表格结构审核
3. 封面视觉信息审核
4. 人员和年龄审核
5. 施工周期审核
6. 应急预案审核
7. 错别字提醒

日期计算、年龄计算、电话格式、记录数量等确定性逻辑使用 Python 工具完成，不依赖模型自由推理。

## 8. 串行与并行策略

### 8.1 串行依赖

```text
文件下载
  -> 视觉资产生成
  -> 视觉识别结果汇总
  -> ConstructionFacts
  -> 规则审核
  -> 交叉校验
  -> 最终报告
```

### 8.2 并行任务

- 多个文件下载和转换
- 同一文件的多个视觉资产识别
- 各类规则 Agent
- 多个低置信度区域的定向复核

Qwen 调用需要配置并发上限，避免接口限流和资源争抢。

## 9. Provider 设计

识别引擎只保留一个简单抽象，不引入能力矩阵：

```text
ExtractorProvider
```

当前实现：

```text
QwenExtractor(model="qwen3.8")
```

后续可新增：

```text
OtherOcrExtractor
OtherVisionExtractor
```

所有 Provider 必须输出相同的 `ExtractionResult`。DeepAgents、事实汇总和规则 Agent 不依赖具体 Provider。

任务配置示例：

```json
{
  "provider": "qwen",
  "model": "qwen3.8",
  "fallback_provider": null,
  "max_retries": 2
}
```

## 10. 失败与重试

### 10.1 可重试失败

- 文件下载超时或网络错误
- 视觉模型临时错误或限流
- 文件渲染临时失败
- 返回 JSON 不合法
- 关键区域置信度过低

### 10.2 不应重试的情况

- 文件确实不存在
- 文件格式不支持
- 业务规则判断为不合规
- 人员年龄确实超过限制
- 每日工作记录确实少于施工天数

### 10.3 重试顺序

```text
同 Provider 重试
  -> fallback_provider（如果配置）
  -> 局部区域重新识别
  -> REVIEW_REQUIRED 或 UNAVAILABLE
```

每次重试都记录：

- Provider
- 模型名称和版本
- attempt 次数
- prompt 版本
- 错误信息
- 输入 asset_id

业务不合规不能因为重试后结果变化而自动覆盖，最终结果必须保留证据和来源。

## 11. 数据对象

建议定义以下内部对象：

```text
ReviewRun
InputManifest
VisualAsset
ExtractionResult
ConstructionFacts
RuleFinding
ReviewReport
```

Agent 之间只传递对象 ID 和版本号，不传递大图片或完整识别文本：

```json
{
  "run_id": "run-001",
  "artifact_id": "facts-001",
  "version": 1
}
```

`RuleFinding` 必须包含：

- `rule_id`
- `severity`
- `status`
- `message`
- `evidence`
- `confidence`
- `retryable`

## 12. 规则严重级别

规则来源为 `deman.md`，建议统一分为：

| 级别 | 处理方式 |
| --- | --- |
| `REJECT` | 影响最终审核结论 |
| `SUGGESTION` | 输出整改建议，不直接否决 |
| `REMINDER` | 输出提醒，不直接否决 |

最终结论按以下顺序计算：

```text
存在 REJECT -> REJECT
否则存在 REVIEW_REQUIRED -> REVIEW_REQUIRED
否则存在 SUGGESTION 或 REMINDER -> PASS_WITH_SUGGESTIONS
否则 -> PASS
```

## 13. 非功能要求

- 同一个 `run_id` 支持中断后恢复。
- 相同文件和相同模型版本的任务应具备幂等性。
- 识别结果和审核结果可追溯到视觉资产及坐标区域。
- 不向模型暴露不必要的业务字段和下载凭证。
- 失败任务不能导致整批已完成任务重复执行。
- Provider、模型名称和 prompt 版本必须写入审计日志。

## 14. 验收标准

### 文件处理

- 只读取 `constructionProgrammeFileInfoList[].s3PreviewFileUrl`。
- 技术交底和动火交底文件不会被下载或审核。
- 无打印页码时仍能通过 `asset_id + bbox` 定位其他审核证据。

### 识别与审核

- Qwen3.8 能够完成文字、表格、签字、公章候选区域、印章文字和勾选识别。
- 公章存在性和印章内容与 `vendorName` 的匹配结果可追溯。
- 公章红色结论必须包含独立图像颜色分析结果，不能只使用 LLM 返回值。
- 识别结果经过结构化校验后才能进入规则审核。
- 七类规则 Agent 可以并行执行。
- 视觉识别失败能够按任务节点重试。
- 低置信度关键结论不会自动判定为通过。

### 可替换性

- 更换 Provider 不需要修改 Supervisor。
- 更换 Provider 不需要修改规则 Agent。
- 新 Provider 输出统一的 `ExtractionResult` 即可接入。

## 15. 后续实施顺序

1. 确认请求和审核报告 JSON 契约。
2. 实现 `s3PreviewFileUrl` 文件获取和视觉资产生成。
3. 实现 `ExtractorProvider` 与 `QwenExtractor`。
4. 实现结构化识别结果和 artifact 持久化。
5. 实现 `ConstructionFacts` 汇总。
6. 实现七类规则 Agent 和错别字提醒。
7. 加入重试、幂等和断点恢复。
8. 使用第二个 OCR/视觉工具验证 Provider 可替换性。
