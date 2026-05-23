"""LLM interface for batched audit checks.

Supports two backends, selected via the ``backend`` parameter:

mlx
    Uses ``mlx_lm`` (Apple Silicon, fastest on M4 Max).
    Requires: ``pip install mlx-lm``

ollama
    Uses the Ollama HTTP API at ``http://localhost:11434``.
    Requires: Ollama running locally with the target model pulled.
    ``model`` should be the Ollama model tag, e.g. ``gemma3:27b``.

Usage::

    gen = Generator(backend="mlx", model="mlx-community/gemma-3-27b-it-4bit")
    response = gen.generate(prompt, max_tokens=512)
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class Generator:
    """Thin wrapper around an LLM backend.

    Args:
        backend: ``'mlx'`` or ``'ollama'``.
        model: Model identifier (MLX repo id or Ollama tag).
        max_tokens: Default token budget for generation.
    """

    def __init__(
        self,
        backend: str = "mlx",
        model: str = "mlx-community/gemma-3-27b-it-4bit",
        max_tokens: int = 512,
    ) -> None:
        self.backend = backend
        self.model = model
        self.max_tokens = max_tokens
        self._mlx_model = None
        self._mlx_tokenizer = None

        if backend == "mlx":
            self._load_mlx()
        elif backend == "ollama":
            self._check_ollama()
        else:
            raise ValueError(f"Unknown backend: {backend!r}. Use 'mlx' or 'ollama'.")

    # ------------------------------------------------------------------
    # Backend initialisation
    # ------------------------------------------------------------------

    def _load_mlx(self) -> None:
        try:
            from mlx_lm import load  # type: ignore[import]
        except ImportError as e:
            raise ImportError(
                "mlx_lm is not installed. Run: pip install mlx-lm"
            ) from e
        log.info("Loading MLX model %s …", self.model)
        self._mlx_model, self._mlx_tokenizer = load(self.model)
        log.info("MLX model loaded.")

    def _check_ollama(self) -> None:
        import urllib.request
        try:
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=3)
        except Exception as e:
            raise RuntimeError(
                "Ollama is not reachable at http://localhost:11434. "
                "Start Ollama and ensure the model is pulled."
            ) from e

    # ------------------------------------------------------------------
    # Generation
    # ------------------------------------------------------------------

    def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        """Generate a response for *prompt*.

        Args:
            prompt: The full prompt string to send to the model.
            max_tokens: Token budget; defaults to ``self.max_tokens``.

        Returns:
            Generated text as a plain string (no surrounding whitespace).
        """
        n = max_tokens or self.max_tokens
        if self.backend == "mlx":
            return self._generate_mlx(prompt, n)
        return self._generate_ollama(prompt, n)

    def _generate_mlx(self, prompt: str, max_tokens: int) -> str:
        from mlx_lm import generate  # type: ignore[import]
        result = generate(
            self._mlx_model,
            self._mlx_tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            verbose=False,
        )
        return result.strip()

    def _generate_ollama(self, prompt: str, max_tokens: int) -> str:
        import json
        import urllib.request

        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"num_predict": max_tokens},
        }).encode()
        req = urllib.request.Request(
            "http://localhost:11434/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
        return data.get("response", "").strip()
