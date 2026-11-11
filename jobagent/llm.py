"""Thin wrapper over the Anthropic SDK: structured output, optional web search, refusal fallback."""

from __future__ import annotations

from typing import TypeVar

import anthropic
from pydantic import BaseModel, ValidationError

from .config import MODEL, candidate_brief

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None

# Server-side refusal fallback: if the primary model declines, the API reruns on a recommended model.
_FALLBACK_HEADERS = {"anthropic-beta": "server-side-fallback-2026-07-01"}
_FALLBACK_BODY = {"fallbacks": "default"}

WEB_SEARCH = {"type": "web_search_20260209", "name": "web_search"}


class LLMError(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()
    return _client


def ask(
    task: str,
    schema: type[T],
    *,
    role: str,
    web_search_uses: int = 0,
    effort: str = "medium",
    max_tokens: int = 16000,
) -> T:
    """Run one task against the candidate brief and return a validated `schema` instance.

    `role` is a short task-specific system instruction; the candidate brief is appended after it
    and marked for prompt caching, so repeated daily calls reuse it cheaply.
    """
    system = [
        {"type": "text", "text": role},
        {"type": "text", "text": candidate_brief(), "cache_control": {"type": "ephemeral"}},
    ]
    tools = [{**WEB_SEARCH, "max_uses": web_search_uses}] if web_search_uses else anthropic.omit
    messages: list[dict] = [{"role": "user", "content": task}]

    output_config = {"effort": effort, "format": {"type": "json_schema", "schema": _schema(schema)}}

    for _ in range(6):  # pause_turn continuations for long web-search turns
        # Streamed so long web-search turns don't hit the SDK's non-streaming timeout guard.
        with client().messages.stream(
            model=MODEL,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            thinking={"type": "adaptive"},
            output_config=output_config,
            messages=messages,
            extra_headers=_FALLBACK_HEADERS,
            extra_body=_FALLBACK_BODY,
        ) as stream:
            resp = stream.get_final_message()
        if resp.stop_reason == "pause_turn":
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
            continue
        if resp.stop_reason == "refusal":
            raise LLMError(f"model declined: {getattr(resp.stop_details, 'category', None)}")
        if resp.stop_reason == "max_tokens":
            raise LLMError("hit max_tokens before finishing")
        # With web search there can be narration text between searches; the JSON answer is the last text block.
        texts = [b.text for b in resp.content if b.type == "text" and b.text.strip()]
        if not texts:
            raise LLMError("no structured output returned")
        try:
            return schema.model_validate_json(texts[-1])
        except ValidationError as e:
            raise LLMError(f"invalid structured output: {e.error_count()} errors") from e
    raise LLMError("too many pause_turn continuations")


def _schema(model: type[BaseModel]) -> dict:
    # Same pydantic → strict JSON schema conversion `messages.parse(output_format=...)` uses internally.
    from anthropic.lib._parse._transform import transform_schema

    return transform_schema(model.model_json_schema())
