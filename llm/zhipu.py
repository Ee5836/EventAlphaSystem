"""Zhipu AI (智谱) LLM provider."""
import json
import logging
import re
import time
from openai import OpenAI
from llm.base import AbstractLLMProvider


class ZhipuProvider(AbstractLLMProvider):
    """Zhipu (GLM) API provider using OpenAI-compatible interface."""

    def __init__(self, config: dict):
        super().__init__(config)
        api_key = config.get("LLM_API_KEY", "")
        base_url = config.get("LLM_API_BASE", "https://open.bigmodel.cn/api/paas/v4")
        self.client = OpenAI(api_key=api_key, base_url=base_url)
        self.max_retries = config.get("LLM_RETRY_COUNT", 3)
        self.timeout = float(config.get("LLM_TIMEOUT") or 30.0)

    def complete(self, system_prompt: str, user_message: str, **kwargs) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]
        return self.chat(messages, **kwargs)

    def complete_json(self, system_prompt: str, user_message: str, **kwargs) -> dict:
        json_instruction = (
            "\n\nIMPORTANT: You MUST respond with ONLY valid JSON. "
            "No markdown code fences, no extra text."
        )
        raw = self.complete(system_prompt + json_instruction, user_message, **kwargs)
        return self._parse_json(raw)

    def chat(self, messages: list[dict], **kwargs) -> str:
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
                    raise ValueError("Zhipu API returned empty choices")
                return response.choices[0].message.content or ""
            except Exception as e:
                if attempt == self.max_retries - 1:
                    raise RuntimeError(f"Zhipu API failed: {e}")
                time.sleep(2 ** attempt)
        raise RuntimeError("Zhipu API: unexpected retry loop exit")

    def embed(self, texts: list[str]) -> list[list[float]]:
        try:
            from sentence_transformers import SentenceTransformer
            model = SentenceTransformer("BAAI/bge-small-zh-v1.5", local_files_only=True)
            embeddings = model.encode(texts, normalize_embeddings=True)
            return embeddings.tolist()
        except Exception:
            logging.getLogger("llm.zhipu").warning(
                "Failed to get embeddings, returning zero vectors"
            )
            return [[0.0] * 768 for _ in texts]

    @staticmethod
    def _parse_json(raw: str) -> dict:
        text = raw.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*\n?", "", text)
            text = re.sub(r"\n?```\s*$", "", text)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
            raise ValueError(f"Failed to parse JSON from Zhipu response: {raw[:500]}")
