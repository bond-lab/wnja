"""MLX-based LLM interface for the wnja audit pipeline.

Uses ``mlx_lm`` with Apple Silicon unified memory (M4 Max target).
Supports both a raw ``generate()`` call and a structured ``chat()`` call
that applies the model's own chat template (system + user turns).

Using chat() is strongly preferred for instruct-tuned models (Gemma 3 it,
Qwen2.5 Instruct) because it applies the correct special tokens and role
delimiters that the model was fine-tuned on.

Usage::

    gen = Generator("mlx-community/gemma-3-27b-it-4bit")
    response = gen.chat(
        system="You are a linguistics expert.",
        user="Evaluate these definitions: ...",
        max_tokens=512,
    )
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


class Generator:
    """Wrapper around mlx_lm for single-model inference.

    Args:
        model: MLX model repo id, e.g. 'mlx-community/gemma-3-27b-it-4bit'
            or 'mlx-community/Qwen2.5-32B-Instruct-4bit'.
        max_tokens: Default generation budget.
        temp: Sampling temperature. Use 0.0 for deterministic output.
    """

    def __init__(
        self,
        model: str = "mlx-community/gemma-3-27b-it-4bit",
        max_tokens: int = 512,
        temp: float = 0.0,
    ) -> None:
        self.model_id = model
        self.max_tokens = max_tokens
        self.temp = temp
        self._model = None
        self._tokenizer = None
        self._load()

    def _load(self) -> None:
        try:
            from mlx_lm import load  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "mlx_lm is not installed. On Apple Silicon run: pip install mlx-lm"
            ) from exc
        log.info("Loading MLX model %s …", self.model_id)
        self._model, self._tokenizer = load(self.model_id)
        log.info("Model loaded.")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        system: str,
        user: str,
        max_tokens: int | None = None,
    ) -> str:
        """Generate a response using the model's chat template.

        Applies ``tokenizer.apply_chat_template`` with system and user roles
        so that instruct models receive properly formatted input.

        Args:
            system: System prompt (task description, output format rules).
            user: User turn (the actual data to evaluate this call).
            max_tokens: Token budget; defaults to ``self.max_tokens``.

        Returns:
            Generated text, stripped of surrounding whitespace.
        """
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        prompt = self._tokenizer.apply_chat_template(
            messages,
            tokenize=False,
            add_generation_prompt=True,
        )
        return self._generate_raw(prompt, max_tokens or self.max_tokens)

    def generate(self, prompt: str, max_tokens: int | None = None) -> str:
        """Generate from a raw prompt string (no chat template applied).

        Use ``chat()`` instead for instruct models.

        Args:
            prompt: Raw prompt string.
            max_tokens: Token budget; defaults to ``self.max_tokens``.

        Returns:
            Generated text, stripped of surrounding whitespace.
        """
        return self._generate_raw(prompt, max_tokens or self.max_tokens)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _generate_raw(self, prompt: str, max_tokens: int) -> str:
        from mlx_lm import generate  # type: ignore[import]
        return generate(
            self._model,
            self._tokenizer,
            prompt=prompt,
            max_tokens=max_tokens,
            temp=self.temp,
            verbose=False,
        ).strip()
