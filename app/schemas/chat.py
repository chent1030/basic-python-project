"""Schemas for the LLM chat endpoints."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None


class ChatResponse(BaseModel):
    content: str
    model: str
