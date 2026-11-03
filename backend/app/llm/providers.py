"""Pluggable LLM providers behind one tiny interface (complete / stream).

Implemented directly over HTTP with httpx so there are no SDK version conflicts:
  ollama     local models (default llama3.1:8b) - free, private, offline
  openai     any OpenAI-compatible endpoint (OpenAI, vLLM, LM Studio, Groq, ...)
  anthropic  Claude models via the Messages API
  extractive no LLM at all: answers are stitched from the retrieved sentences. Used for
             tests and so the full app runs on a machine with nothing installed.
"""
from __future__ import annotations

import json
from collections.abc import Iterator

import httpx

from app.config import Settings


class LLM:
    name = "base"

    def complete(self, system: str, user: str, max_tokens: int = 1024, json_mode: bool = False) -> str:
        return "".join(self.stream(system, user, max_tokens, json_mode))

    def stream(self, system: str, user: str, max_tokens: int = 1024, json_mode: bool = False) -> Iterator[str]:
        raise NotImplementedError


class OllamaLLM(LLM):
    def __init__(self, model: str, url: str, temperature: float, timeout: float):
        self.name = f"ollama:{model}"
        self.model, self.url, self.temperature, self.timeout = model, url.rstrip("/"), temperature, timeout

    def stream(self, system, user, max_tokens=1024, json_mode=False):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": True,
            "options": {"temperature": self.temperature, "num_predict": max_tokens, "num_ctx": 8192},
        }
        if json_mode:
            body["format"] = "json"
        with httpx.stream("POST", f"{self.url}/api/chat", json=body, timeout=self.timeout) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line:
                    continue
                data = json.loads(line)
                if data.get("message", {}).get("content"):
                    yield data["message"]["content"]
                if data.get("done"):
                    break


class OpenAILLM(LLM):
    def __init__(self, model: str, api_key: str, base_url: str, temperature: float, timeout: float):
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for LLM_PROVIDER=openai")
        self.name = f"openai:{model}"
        self.model, self.base_url, self.temperature, self.timeout = model, base_url.rstrip("/"), temperature, timeout
        self.headers = {"Authorization": f"Bearer {api_key}"}

    def stream(self, system, user, max_tokens=1024, json_mode=False):
        body = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        with httpx.stream(
            "POST", f"{self.base_url}/chat/completions", json=body, headers=self.headers, timeout=self.timeout
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                delta = json.loads(line[6:])["choices"][0].get("delta", {}).get("content")
                if delta:
                    yield delta


class AnthropicLLM(LLM):
    def __init__(self, model: str, api_key: str, temperature: float, timeout: float,
                 base_url: str = "https://api.anthropic.com"):
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for LLM_PROVIDER=anthropic")
        self.name = f"anthropic:{model}"
        self.model, self.temperature, self.timeout = model, temperature, timeout
        self.base_url = base_url.rstrip("/")
        self.headers = {"x-api-key": api_key, "anthropic-version": "2023-06-01"}

    def stream(self, system, user, max_tokens=1024, json_mode=False):
        if json_mode:
            system += "\nRespond with a single valid JSON object and nothing else."
        body = {
            "model": self.model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "temperature": self.temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        with httpx.stream(
            "POST", f"{self.base_url}/v1/messages", json=body, headers=self.headers, timeout=self.timeout
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line[6:])
                if event.get("type") == "content_block_delta" and event["delta"].get("type") == "text_delta":
                    yield event["delta"]["text"]


class ExtractiveLLM(LLM):
    """Marker class: generation modules detect it and use deterministic, LLM-free logic."""

    name = "extractive"

    def stream(self, system, user, max_tokens=1024, json_mode=False):
        raise RuntimeError("ExtractiveLLM is handled by the generation layer directly")


def make_llm(s: Settings, provider: str | None = None, model: str | None = None) -> LLM:
    provider = provider or s.llm_provider
    model = model or s.default_llm_model()
    if provider == "ollama":
        return OllamaLLM(model, s.ollama_url, s.llm_temperature, s.llm_timeout_s)
    if provider == "openai":
        return OpenAILLM(model, s.openai_api_key, s.openai_base_url, s.llm_temperature, s.llm_timeout_s)
    if provider == "anthropic":
        return AnthropicLLM(model, s.anthropic_api_key, s.llm_temperature, s.llm_timeout_s, s.anthropic_base_url)
    if provider == "extractive":
        return ExtractiveLLM()
    raise ValueError(f"Unknown LLM_PROVIDER: {provider}")
