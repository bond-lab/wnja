"""Ollama-based LLM interface for the wnja audit pipeline.

Uses the native Ollama /api/generate endpoint via httpx.
Thinking mode is disabled (think=False) for speed.
Pass a Pydantic model class (or raw JSON Schema dict) via ``schema`` for
structured output — no format instructions needed in the prompt.

Usage::

    from pydantic import BaseModel
    from typing import Literal

    class Reply(BaseModel):
        verdict: Literal["OK", "BAD"]

    gen = Generator("qwen3.5:latest")
    response = gen.chat(
        system="You are a reviewer.",
        user="Is this correct?",
        schema=Reply,
    )
    result = Reply.model_validate_json(response)
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

log = logging.getLogger(__name__)

_OLLAMA_BASE_URL = "http://localhost:11434"


class Generator:
    """Wrapper around the Ollama /api/generate endpoint.

    Args:
        model: Ollama model tag, e.g. 'qwen3.5:latest' or 'gemma4:latest'.
        max_tokens: Default generation budget (num_predict).
        temp: Sampling temperature. Defaults to 0.0 for deterministic output.
        base_url: Ollama server base URL.
    """

    def __init__(
        self,
        model: str = "qwen3.5:latest",
        max_tokens: int = 512,
        temp: float = 0.0,
        base_url: str = _OLLAMA_BASE_URL,
    ) -> None:
        self.model_id = model
        self.max_tokens = max_tokens
        self.temp = temp
        self._base_url = base_url.rstrip("/")
        log.info("Ollama generator: model=%s base_url=%s", model, base_url)

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
        schema: "type[BaseModel] | dict | None" = None,
        think: bool = False,
    ) -> str:
        """Generate a response using the Ollama generate API.

        Args:
            system: System prompt (task description; no format instructions needed
                    when ``schema`` is provided — the schema enforces the format).
            user: User turn content.
            max_tokens: Token budget; defaults to self.max_tokens.
            schema: Pydantic model class or raw JSON Schema dict. When provided,
                    the model output is constrained to match this schema and
                    will always be valid JSON.
            think: Enable extended thinking mode (default False). When True the
                   model reasons step-by-step before answering; this can improve
                   accuracy at the cost of significantly more tokens and time.

        Returns:
            Generated text, stripped of surrounding whitespace.
        """
        try:
            import httpx
        except ImportError as exc:
            raise ImportError("httpx is not installed. Run: uv add httpx") from exc

        fmt: dict | None = None
        if schema is not None:
            if isinstance(schema, dict):
                fmt = schema
            else:
                fmt = schema.model_json_schema()

        payload: dict = {
            "model": self.model_id,
            "think": think,
            "system": system,
            "prompt": user,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "temperature": self.temp,
                "num_predict": max_tokens or self.max_tokens,
            },
        }
        if fmt is not None:
            if think:
                # Ollama 0.21.2: format constraints are incompatible with think=True;
                # the response comes back empty. Drop the schema and let the caller
                # handle unstructured output.
                log.warning(
                    "think=True is incompatible with schema enforcement in Ollama 0.21.2 "
                    "— schema ignored; model output will be unstructured."
                )
            else:
                payload["format"] = fmt

        with httpx.Client(timeout=300.0) as client:
            resp = client.post(f"{self._base_url}/api/generate", json=payload)
            resp.raise_for_status()

        return resp.json()["response"].strip()
