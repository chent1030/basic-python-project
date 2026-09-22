from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..domain.models import digest


class Provider(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["openai", "anthropic", "ollama"] = "openai"
    base_url: str
    api_key: str = ""
    api_key_env: str | None = None
    timeout: float = Field(default=120, gt=0)
    max_retries: int = Field(default=2, ge=0, le=10)

    @model_validator(mode="after")
    def validate_url(self) -> Provider:
        url = urlsplit(self.base_url)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("Model base_url must be an absolute HTTP(S) endpoint")
        if url.username or url.password or url.query or url.fragment:
            raise ValueError("Model endpoints must not contain credentials, queries or fragments")
        return self


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    model: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    capabilities: set[str] = Field(default_factory=lambda: {"tools", "structured_output"})
    fallbacks: tuple[str, ...] = ()


class ModelConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid")
    providers: dict[str, Provider]
    models: dict[str, ModelProfile]
    agents: dict[str, str] = Field(default_factory=dict)

    @staticmethod
    def validate_parameters(value: Any) -> None:
        if isinstance(value, dict):
            forbidden = {
                "model",
                "model_name",
                "base_url",
                "api_key_env",
                "authorization",
                "default_headers",
                "http_client",
                "http_async_client",
            }
            if any(key.lower() in forbidden for key in value):
                raise ValueError(
                    "Model parameters may not override identity, transport or credentials"
                )
            for item in value.values():
                ModelConfiguration.validate_parameters(item)
        elif isinstance(value, list):
            for item in value:
                ModelConfiguration.validate_parameters(item)

    @model_validator(mode="after")
    def validate_references(self) -> ModelConfiguration:
        for profile in self.models.values():
            self.validate_parameters(profile.parameters)
            if profile.provider not in self.providers:
                raise ValueError(f"Unknown model provider: {profile.provider}")
            forbidden = {"model", "model_name", "base_url", "api_key_env"}
            if forbidden & profile.parameters.keys():
                raise ValueError("Endpoint/model/credentials must not be overridden in parameters")
            if any(name not in self.models for name in profile.fallbacks):
                raise ValueError("Unknown fallback profile")
            if any(self.models[name].fallbacks for name in profile.fallbacks):
                raise ValueError("Fallback chains must be flat and nonrecursive")
        for profile_name in self.agents.values():
            if profile_name not in self.models:
                raise ValueError(f"Unknown agent model profile: {profile_name}")
        return self

    @classmethod
    def load(cls, path: str | Path) -> ModelConfiguration:
        return cls.model_validate(yaml.safe_load(Path(path).read_text(encoding="utf-8")))


class Models:
    def __init__(self, config: ModelConfiguration):
        self.config = config

    def snapshot(self, agent: Any, profile_name: str | None = None) -> dict[str, Any]:
        name = profile_name or self.config.agents.get(agent.name, agent.model_profile)
        profile = self.config.models[name]
        provider = self.config.providers[profile.provider]
        required = {"tools"}
        if agent.output_schema:
            required.add("structured_output")
        if not required <= profile.capabilities:
            raise ValueError(f"Model {name} lacks required capabilities {required}")
        return {
            "profile": name,
            "model": profile.model,
            "provider": profile.provider,
            "kind": provider.kind,
            "base_url": provider.base_url,
            "parameters": profile.parameters,
            "timeout": provider.timeout,
            "max_retries": provider.max_retries,
            "fallbacks": [self.snapshot(agent, fallback) for fallback in profile.fallbacks],
            "capabilities": sorted(profile.capabilities),
        }

    def create(self, agent: Any, profile_name: str | None = None) -> BaseChatModel:
        snapshot = self.snapshot(agent, profile_name)
        provider = self.config.providers[snapshot["provider"]]
        # 凭据读取优先级:环境变量(api_key_env) > yaml api_key。
        # 安全提醒(R-N2):yaml 明文 key 一旦入库即视为泄露,应尽快在供应商侧
        # 轮换并改用环境变量投递(config/agents.yaml 注释同步此提醒)。
        key = os.environ.get(provider.api_key_env) if provider.api_key_env else None
        key = key or provider.api_key or None
        if provider.api_key_env and not key:
            raise ValueError(
                f"Missing provider credential environment variable {provider.api_key_env}"
            )
        parameters = dict(snapshot["parameters"])
        if provider.kind == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=snapshot["model"],
                base_url=provider.base_url,
                api_key=key or "local",
                timeout=provider.timeout,
                max_retries=provider.max_retries,
                **parameters,
            )
        if provider.kind == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model_name=snapshot["model"],
                base_url=provider.base_url,
                api_key=key,
                timeout=provider.timeout,
                max_retries=provider.max_retries,
                **parameters,
            )
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=snapshot["model"],
            base_url=provider.base_url,
            client_kwargs={"timeout": provider.timeout},
            **parameters,
        )


class ProfiledModel(BaseChatModel):
    inner: Any = Field(exclude=True)
    policy_key: str
    model_name: str

    @property
    def _llm_type(self) -> str:
        return "framework-profiled"

    def _get_ls_params(self, stop: Any = None, **kwargs: Any) -> dict[str, Any]:
        return {**self.inner._get_ls_params(stop=stop, **kwargs), "ls_provider": self.policy_key}

    def _generate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        return self.inner._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    async def _agenerate(
        self, messages: Any, stop: Any = None, run_manager: Any = None, **kwargs: Any
    ) -> Any:
        return await self.inner._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        return self.inner.bind_tools(tools, **kwargs)

    def with_structured_output(self, schema: Any, **kwargs: Any) -> Any:
        return self.inner.with_structured_output(schema, **kwargs)


def apply_profile(
    model: BaseChatModel, *, planning: bool, general_purpose: bool, profile: Any = None
) -> BaseChatModel:
    from deepagents.profiles import (
        GeneralPurposeSubagentProfile,
        HarnessProfile,
        register_harness_profile,
    )

    policy_key = "framework_" + digest([planning, general_purpose, repr(profile)])[:24]
    register_harness_profile(policy_key, profile or HarnessProfile())
    register_harness_profile(
        policy_key,
        HarnessProfile(
            excluded_tools=frozenset() if planning else frozenset({"write_todos"}),
            general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=general_purpose),
        ),
    )
    return ProfiledModel(
        inner=model,
        model_name=getattr(model, "model_name", "model"),
        policy_key=policy_key,
        profile=getattr(model, "profile", None),
    )
