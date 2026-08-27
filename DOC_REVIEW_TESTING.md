# 文档审核测试

## 离线测试

离线测试不会访问文件地址、MinerU 或 LLM，覆盖 Blackboard 生命周期、35条规则、
失败关闭策略、四态勾选、文件字段别名、多文件图片聚合、超时和主流程编排。

```bash
uv run pytest -q tests/test_blackboard_context.py \
  tests/test_doc_review_rules.py tests/test_doc_review_service.py \
  tests/test_doc_review_boundaries.py
```

## 真实环境准备

在 `config/local.yaml` 配置可访问的 `mineru.url` 和支持图文输入的
`llm.providers`。若业务涉及防护用品或动火作业，还需根据正式业务模板填写
`config/doc_review_rules.yaml`：

- `ppe_requirements`：每种作业类型对应的必备PPE；
- `hot_work_ppe`：动火作业必备PPE；
- `hot_work_risk_template`：动火风险分析标准模板全文。

请求数据应提供 `processInitiatedAt`，也可以通过脚本的 `--initiation-time` 传入。
动火作业数据应在 `hotWorkTechDiscloseFileInfoList` 中提供动火交底附件。

## 第一步：只测试下载和 MinerU

```bash
uv run python scripts/test_doc_review_ocr.py \
  --data data.json \
  --output-dir doc-review-ocr-output
```

命令成功时退出码为0，并在输出目录生成每份文件的 Markdown 和 `summary.json`。
逐份确认 `error` 为空、`markdown_chars` 大于0；需要公章或签名视觉检查的图片文件还应
包含 `image_keys`。诊断目录可能包含业务敏感文本，测试结束后按环境的数据规范处理。

## 第二步：端到端测试

```bash
uv run python scripts/test_doc_review_integration.py \
  --data data.json \
  --initiation-time 2026-08-10T09:30:00 \
  --output doc-review-report.json
```

脚本会依次下载文档、串行调用 MinerU、调用提取/错别字/视觉模型，并输出完整报告。
输出中的 `diagnostics` 会列出缺失、重复、不可验证的规则及服务警告。

正式验收建议启用严格模式：

```bash
uv run python scripts/test_doc_review_integration.py \
  --data data.json \
  --initiation-time 2026-08-10T09:30:00 \
  --output doc-review-report.json \
  --strict
```

严格模式要求业务基准已配置，并在出现服务警告、不可验证规则、规则缺失或重复时返回
非0退出码。业务材料本身违反规则产生 `FAIL` 是正常审核结果，不会被当成测试程序故障。

## Pytest真实环境入口

```bash
RUN_DOC_REVIEW_LIVE=1 \
DOC_REVIEW_DATA=data.json \
DOC_REVIEW_INITIATION_TIME=2026-08-10T09:30:00 \
uv run pytest -q -s tests/test_doc_review_live.py
```

真实测试不强制业务结论必须通过，但要求流程完成、35条规则各产生且只产生一个结果。
需要把警告和不可验证规则也作为测试失败时，增加：

```bash
RUN_DOC_REVIEW_LIVE=1 \
DOC_REVIEW_LIVE_STRICT=1 \
DOC_REVIEW_DATA=data.json \
DOC_REVIEW_INITIATION_TIME=2026-08-10T09:30:00 \
uv run pytest -q -s tests/test_doc_review_live.py
```

## 结果判读

- `REJECTED`：至少一个否决规则存在明确不合规证据；
- `MANUAL_REVIEW`：没有明确否决失败，但至少一个否决规则证据不足；
- `PASSED`：所有适用的否决规则均有充分证据且通过；
- `UNVERIFIABLE`：证据、业务模板或外部服务结果不足，不等同于通过；
- `NOT_APPLICABLE`：有明确证据证明规则不适用，例如施工方案明确未勾选动火作业。
