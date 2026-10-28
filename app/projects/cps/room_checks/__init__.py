"""B6 辅房视觉点检项目（契约 C-04 room-checks/judge）。

分层与 initial_review/inspection_plans 对齐：
- domain：请求/结果模型 + C-04 状态枚举 + 视觉结构化输出 schema；
- application：判定服务（幂等 + 两阶段判定链编排）；
- infrastructure：prompt 常量（版本化）。

模型客户端与 RustFS 取图复用 initial_review 基建（model_client.py），不重复造轮。
"""
