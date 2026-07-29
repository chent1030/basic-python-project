"""Prompt loader & renderer.

Prompts live as files under `prompts/` (configurable via config.yaml).
File name (without extension) is the prompt identifier.

Supported file formats (auto-detected by extension):
  .yaml / .yml   Structured prompt: see translate.yaml for the full schema.
                 Fields: name, description, system, user, temperature,
                 model, max_tokens, variables[]
  .txt / .text   Plain text — the whole file becomes the `user` message.
                 Optional leading front-matter block delimited by `---`.
  .j2 / .jinja2  Jinja2 template (treated like .txt but rendered via Jinja2).

Variable rendering rules (apply to all formats):
  - If Jinja2 is installed, render with Jinja2 syntax: {{ var }}, {% if %}...
  - Otherwise fall back to str.format with {var} syntax.

Usage:
    from app.core.prompt import load_prompt, render_prompt

    # Get a structured PromptTemplate (with declared variables metadata)
    tpl = load_prompt("translate")
    rendered = tpl.render(source="Hello", target_lang="中文")
    # rendered.system, rendered.user, rendered.temperature, rendered.model

    # Or one-shot:
    rendered = render_prompt("translate", source="Hello", target_lang="中文")

    # Inject straight into the LLM:
    await llm_client.chat_prompt("translate", source="Hello", target_lang="中文")
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.config import settings

log = logging.getLogger("app.prompt")

# Optional Jinja2 support.
try:
    from jinja2 import Environment, StrictUndefined, Template
    _JINJA = Environment(
        autoescape=False,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
        trim_blocks=False,
        lstrip_blocks=False,
    )
except ImportError:  # pragma: no cover
    _JINJA = None
    Template = None  # type: ignore[assignment]


_PROMPT_DIR_CACHE: Path | None = None
_LOADED_CACHE: dict[str, PromptTemplate] = {}


def _prompt_dir() -> Path:
    """Resolve the prompts directory relative to project root."""
    global _PROMPT_DIR_CACHE
    if _PROMPT_DIR_CACHE is not None:
        return _PROMPT_DIR_CACHE
    # app/core/prompt.py -> project root is parents[2]
    root = Path(__file__).resolve().parents[2]
    p = (root / settings.prompts.dir).resolve()
    if not p.exists():
        log.warning("Prompts directory does not exist: %s", p)
    _PROMPT_DIR_CACHE = p
    return p


@dataclass
class PromptVariable:
    name: str
    description: str = ""
    required: bool = True
    default: Any = None


@dataclass
class PromptTemplate:
    """A loaded prompt, ready to be rendered with variables."""

    name: str
    system: str | None = None
    user: str | None = None
    temperature: float | None = None
    model: str | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    variables: list[PromptVariable] = field(default_factory=list)
    source_path: Path | None = None
    _raw_user: str | None = None     # unrendered user text (for caching)
    _raw_system: str | None = None   # unrendered system text

    # ---------- rendering ------------------------------------------------
    def render(self, **variables: Any) -> RenderedPrompt:
        """Render system & user with the given variables.

        Missing required variables raise ValueError. Variables not declared
        in the prompt file are still accepted (forward-compatible).
        """
        merged = self._merge_variables(variables)
        self._validate_required(merged)

        rendered_system = self._render_text(self._raw_system, merged) if self._raw_system else None
        rendered_user = self._render_text(self._raw_user, merged) if self._raw_user else None

        return RenderedPrompt(
            name=self.name,
            system=rendered_system,
            user=rendered_user,
            temperature=self.temperature,
            model=self.model,
            max_tokens=self.max_tokens,
            extra=dict(self.extra),
            variables=merged,
        )

    # ---------- internals ------------------------------------------------
    def _merge_variables(self, provided: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        # Start from declared defaults.
        for var in self.variables:
            merged[var.name] = var.default
        # Overlay caller-provided values.
        merged.update({k: v for k, v in provided.items() if v is not None})
        return merged

    def _validate_required(self, variables: dict[str, Any]) -> None:
        missing = [
            var.name
            for var in self.variables
            if var.required and variables.get(var.name) is None
        ]
        if missing:
            raise ValueError(
                f"Prompt '{self.name}' is missing required variables: {missing}"
            )

    @staticmethod
    def _render_text(text: str, variables: dict[str, Any]) -> str:
        if not text:
            return text
        if _JINJA is not None:
            return _JINJA.from_string(text).render(**variables)
        # Fallback: str.format — ignore missing keys gracefully.
        try:
            return text.format_map(_SafeDict(variables))
        except Exception:
            return text


class _SafeDict(dict):
    """dict subclass that returns "{key}" for missing keys (defensive)."""

    def __missing__(self, key: str) -> str:  # noqa: D401
        return "{" + key + "}"


@dataclass
class RenderedPrompt:
    """A prompt with all variables substituted, ready for the LLM."""

    name: str
    system: str | None
    user: str | None
    temperature: float | None = None
    model: str | None = None
    max_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    variables: dict[str, Any] = field(default_factory=dict)

    def to_messages(self) -> list[dict[str, str]]:
        """Convert to OpenAI-style chat messages list."""
        msgs: list[dict[str, str]] = []
        if self.system:
            msgs.append({"role": "system", "content": self.system})
        if self.user:
            msgs.append({"role": "user", "content": self.user})
        return msgs


# ---------- loading ---------------------------------------------------------
_FRONT_MATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n(.*)$", re.DOTALL)


def _find_prompt_file(name: str) -> Path:
    """Locate a prompt file by name. Tries known extensions in order."""
    base = _prompt_dir() / name
    # Prefer YAML (structured), then txt, then j2.
    for ext in (".yaml", ".yml", ".txt", ".text", ".j2", ".jinja2"):
        candidate = base.with_suffix(ext)
        if candidate.exists():
            return candidate
    # Allow callers to pass a subpath like "category/name".
    raise FileNotFoundError(
        f"Prompt '{name}' not found under {_prompt_dir()} "
        f"(tried extensions: yaml, yml, txt, text, j2, jinja2)"
    )


def _parse_yaml_prompt(path: Path, raw_text: str) -> PromptTemplate:
    data = yaml.safe_load(raw_text) or {}
    variables = [
        PromptVariable(
            name=v["name"],
            description=v.get("description", ""),
            required=v.get("required", True),
            default=v.get("default"),
        )
        for v in (data.get("variables") or [])
    ]
    return PromptTemplate(
        name=data.get("name", path.stem),
        system=data.get("system"),
        user=data.get("user"),
        temperature=data.get("temperature"),
        model=data.get("model"),
        max_tokens=data.get("max_tokens"),
        extra={k: v for k, v in data.items()
               if k not in {"name", "system", "user", "temperature",
                             "model", "max_tokens", "variables"}},
        variables=variables,
        source_path=path,
        _raw_system=data.get("system"),
        _raw_user=data.get("user"),
    )


def _parse_text_prompt(path: Path, raw_text: str, *, is_jinja: bool = False) -> PromptTemplate:
    """Plain-text prompt. Optional YAML front-matter for metadata."""
    front_matter: dict[str, Any] = {}
    body = raw_text
    match = _FRONT_MATTER_RE.match(raw_text)
    if match:
        try:
            front_matter = yaml.safe_load(match.group(1)) or {}
            body = match.group(2)
        except yaml.YAMLError:
            # Not actually front-matter — keep the raw text untouched.
            pass

    variables = [
        PromptVariable(
            name=v["name"],
            description=v.get("description", ""),
            required=v.get("required", True),
            default=v.get("default"),
        )
        for v in (front_matter.get("variables") or [])
    ]
    _ = is_jinja  # jinja handled centrally in _render_text
    return PromptTemplate(
        name=front_matter.get("name", path.stem),
        system=front_matter.get("system"),
        user=body.strip(),
        temperature=front_matter.get("temperature"),
        model=front_matter.get("model"),
        max_tokens=front_matter.get("max_tokens"),
        extra={k: v for k, v in front_matter.items()
               if k not in {"name", "system", "user", "temperature",
                             "model", "max_tokens", "variables"}},
        variables=variables,
        source_path=path,
        _raw_system=front_matter.get("system"),
        _raw_user=body.strip(),
    )


def load_prompt(name: str, *, refresh: bool = False) -> PromptTemplate:
    """Load a prompt template by name (file-name based).

    Args:
        name: Prompt identifier — the filename without extension.
              Subdirectories supported: `load_prompt("support/intent")`.
        refresh: Force reload, bypassing the in-memory cache.
    """
    if not refresh and settings.prompts.cache and name in _LOADED_CACHE:
        return _LOADED_CACHE[name]

    path = _find_prompt_file(name)
    raw_text = path.read_text(encoding="utf-8")

    suffix = path.suffix.lower()
    if suffix in (".yaml", ".yml"):
        tpl = _parse_yaml_prompt(path, raw_text)
    elif suffix in (".j2", ".jinja2"):
        tpl = _parse_text_prompt(path, raw_text, is_jinja=True)
    else:  # .txt / .text
        tpl = _parse_text_prompt(path, raw_text)

    if settings.prompts.cache:
        _LOADED_CACHE[name] = tpl
    log.debug("Loaded prompt '%s' from %s", name, path)
    return tpl


def render_prompt(name: str, *, refresh: bool = False, **variables: Any) -> RenderedPrompt:
    """Convenience: load + render in one call."""
    tpl = load_prompt(name, refresh=refresh)
    return tpl.render(**variables)


def list_prompts() -> list[str]:
    """List all available prompt names (without extension)."""
    root = _prompt_dir()
    if not root.exists():
        return []
    names: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in (
            ".yaml", ".yml", ".txt", ".text", ".j2", ".jinja2"
        ):
            names.append(str(path.relative_to(root).with_suffix("")).replace("\\", "/"))
    return names


def clear_cache() -> None:
    """Clear the prompt cache (useful in tests or after editing files)."""
    _LOADED_CACHE.clear()


__all__ = [
    "PromptTemplate",
    "PromptVariable",
    "RenderedPrompt",
    "load_prompt",
    "render_prompt",
    "list_prompts",
    "clear_cache",
]
