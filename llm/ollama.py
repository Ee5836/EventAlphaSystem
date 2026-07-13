"""Ollama local LLM provider using native /api/chat endpoint.

Uses Ollama native API (not OpenAI-compatible) to handle Qwen3.5 thinking mode:
- Qwen3.5 models send ALL output to `thinking` field, leaving `content` empty
- This provider extracts the final answer from thinking when content is empty
"""

import json
import logging
import re
import time

import requests

from llm.base import AbstractLLMProvider

logger = logging.getLogger("llm.ollama")

def _extract_final_answer(thinking: str) -> str:
    """Extract the final Chinese answer from a Qwen3.5 thinking block.

    The thinking process typically ends with a polished Chinese answer.
    This function strips the English chain-of-thought preamble.
    """
    if not thinking:
        return ""

    # Remove the leading "Thinking Process:" header block
    # Everything before the first substantial Chinese paragraph is preamble
    lines = thinking.split("\n")

    # Find where the actual answer starts — lines with majority CJK characters
    answer_start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        cjk = sum(1 for c in stripped if '一' <= c <= '鿿' or '　' <= c <= '〿')
        total = sum(1 for c in stripped if c.isalpha() or '一' <= c <= '鿿')
        # If line is >50% CJK, consider it the answer start
        if total > 0 and cjk / max(total, 1) > 0.5:
            answer_start = i
            break

    if answer_start > 0:
        result = "\n".join(line.strip() for line in lines[answer_start:] if line.strip())
        if result:
            return result

    return thinking.strip()


class OllamaProvider(AbstractLLMProvider):
    """Ollama local inference using native /api/chat (handles thinking mode).

    Uses Ollama's native API at {base_url}/../api/chat to access both
    `thinking` and `content` fields for Qwen3.5 models.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        base_url = config.get("LLM_API_BASE", "http://localhost:11434/v1")
        # Derive native API base from OpenAI-compatible URL
        self.api_base = base_url.rstrip("/").rsplit("/v1", 1)[0]
        self.chat_url = f"{self.api_base}/api/chat"
        self.max_retries = config.get("LLM_RETRY_COUNT", 3)
        self.request_timeout = float(config.get("LLM_TIMEOUT") or 300.0)

    def complete(self, system_prompt: str, user_message: str, **kwargs) -> str:
        """Single-turn text completion."""
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": user_message})
        return self.chat(messages, **kwargs)

    def complete_json(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        """Completion with JSON parsing."""
        json_instruction = (
            "\n\nIMPORTANT: You MUST respond with ONLY valid JSON. "
            "No markdown, no thinking, no extra text. Output raw JSON only."
        )
        full_system = (system_prompt or "") + json_instruction
        raw = self.complete(full_system, user_message, **kwargs)
        return self._parse_json(raw)

    def chat(self, messages: list[dict], **kwargs) -> str:
        """Multi-turn chat via Ollama native /api/chat."""
        temperature = kwargs.get("temperature", self.temperature)
        max_tokens = kwargs.get("max_tokens", self.max_tokens)

        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }

        for attempt in range(self.max_retries):
            try:
                resp = requests.post(
                    self.chat_url,
                    json=payload,
                    timeout=self.request_timeout,
                )
                resp.raise_for_status()
                data = resp.json()
                msg = data.get("message", {})

                # Qwen3.5 puts output in `thinking`, leaving `content` empty
                content = msg.get("content", "") or ""
                thinking = msg.get("thinking", "") or ""

                if content.strip():
                    return content

                if thinking.strip():
                    return _extract_final_answer(thinking)

                # Both empty — model produced nothing useful
                logger.warning(
                    f"Ollama returned empty content+thinking "
                    f"(eval_count={data.get('eval_count', 0)})"
                )
                return ""

            except requests.exceptions.Timeout:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Ollama timed out after {self.request_timeout}s"
                    )
                time.sleep(5)
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(
                        f"Ollama API failed after {self.max_retries} attempts: {e}"
                    )
                time.sleep(3 * (2 ** attempt))

        raise RuntimeError("Ollama API: unexpected retry loop exit")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embeddings: skip Ollama, go direct to local model."""
        return self._local_embed(texts)

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """Extract and parse JSON from LLM response.

        Handles Qwen3.5 thinking remnants — strips English reasoning lines
        and extracts JSON block.
        """
        text = raw.strip()
        if not text:
            raise ValueError("Empty LLM response — cannot parse JSON")

        # Strip markdown code fences
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)

        # If text contains JSON, try to extract it
        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON array or object
        for pattern in [r"\[.*\]", r"\{.*\}"]:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    continue

        raise ValueError(
            f"Failed to parse JSON from LLM response: {text[:500]}"
        )
