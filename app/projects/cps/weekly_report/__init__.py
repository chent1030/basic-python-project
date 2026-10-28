"""周报项目（C2 — §21 / 设计 §3.3）。

应用层（``application/``）只调 Skill + DataFetcher + Repository + Uploader + Pusher，
不直接依赖 SQLAlchemy / httpx / RustFS / Java 客户端。
"""