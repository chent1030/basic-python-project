from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from pydantic import Field, model_validator

from app.projects.cps.domain.contracts import Contract


class EmbeddingsConfig(Contract):
    url: str | None = None
    model: str = ""
    api_key_env: str | None = None
    timeout: float = Field(default=30, gt=0, le=300)
    max_dimensions: int = Field(default=8192, ge=1, le=65536)

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.url:
            parsed = urlsplit(self.url)
            if parsed.scheme not in ("http", "https") or not parsed.hostname or not self.model:
                raise ValueError("Image embeddings require an HTTP(S) endpoint and a model")
            if parsed.username or parsed.password or parsed.query or parsed.fragment:
                raise ValueError(
                    "Embedding endpoints must not contain credentials, queries or fragments"
                )
        return self


class CPSConfig(Contract):
    """CPS 项目业务配置。

    内网直连口径（用户要求）：Java 后端只需配置 Python 侧地址即可调用
    ``/api/v1/agent*``，不再有共享密钥、IP 白名单或 ``internal_trust`` 开关
    （见 ``app/api/v1/endpoints/agent_runs.py::principal``）。
    """

    enabled: bool = True
    max_rounds: int = Field(default=40, ge=1, le=200)
    max_image_bytes: int = Field(default=10_000_000, ge=1024, le=10_000_000)
    max_images: int = Field(default=20, ge=1, le=50)
    history_days: int = Field(default=90, ge=1, le=3650)
    memory_limit: int = Field(default=5, ge=1, le=50)
    embeddings: EmbeddingsConfig = Field(default_factory=EmbeddingsConfig)

    @classmethod
    def load(cls) -> CPSConfig:
        path = Path(os.environ.get("CPS_CONFIG", "config/cps.yaml"))
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
