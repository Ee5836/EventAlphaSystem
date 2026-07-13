"""DeepSeek LLM provider (OpenAI-compatible API)."""
import json
import re
import time
from typing import Any

from openai import OpenAI

from llm.base import AbstractLLMProvider


class DeepSeekProvider(AbstractLLMProvider):
    """DeepSeek API provider using OpenAI-compatible interface."""

    def __init__(self, config: dict):
        super().__init__(config)
        api_key = config.get("LLM_API_KEY", "")
        base_url = config.get("LLM_API_BASE", "https://api.deepseek.com/v1")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.max_retries = config.get("LLM_RETRY_COUNT", 3)
        self.timeout = float(config.get("LLM_TIMEOUT") or 30.0)

    def complete(self, system_prompt: str, user_message: str, **kwargs) -> str:
        """Single-turn text completion with retry logic."""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        return self.chat(messages, **kwargs)

    def complete_json(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        """Completion with JSON parsing and validation."""
        # Ensure system prompt requests JSON output
        json_instruction = (
            "\n\nIMPORTANT: You MUST respond with ONLY valid JSON. "
            "No markdown code fences, no extra text before or after the JSON object."
        )
        full_system = system_prompt + json_instruction

        raw = self.complete(full_system, user_message, **kwargs)
        return self._parse_json(raw)

    def chat(self, messages: list[dict], **kwargs) -> str:
        """Multi-turn chat with exponential backoff retry."""
        temperature = kwargs.get("temperature", self.temperature)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)

        for attempt in range(self.max_retries):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    timeout=self.timeout,
                )
                if not response.choices:
                    raise ValueError("DeepSeek API returned empty choices")
                return response.choices[0].message.content or ""
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"DeepSeek API failed after {self.max_retries} attempts: {e}"
                    )
                wait = 2 ** attempt
                time.sleep(wait)

        raise RuntimeError("DeepSeek API: unexpected retry loop exit")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Get embeddings via DeepSeek API or fall back to local model."""
        try:
            response = self.client.embeddings.create(
                model="deepseek-embedding", input=texts
            )
            return [d.embedding for d in response.data]
        except Exception:
            # Fallback to local sentence-transformers
            return self._local_embed(texts)

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Extract and parse JSON from LLM response."""
        # Strip markdown code fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"Failed to parse JSON from LLM response: {raw[:500]}")
