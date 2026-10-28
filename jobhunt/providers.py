"""Provider-agnostic LLM clients.

One tiny interface, five backends. Screening and drafting each pick their own
provider from env vars, so you can point the cheap pass at Groq or Gemini and
keep Claude for the expensive drafting pass - or run the whole thing on a free
tier with no card on file.

    complete(system, user)            -> str   (every provider)
    complete_document(prompt, pdf)    -> str   (Anthropic + Gemini only)

Nothing here parses JSON or knows what a Job is. That lives in llm.py.
"""
from __future__ import annotations

import base64
import json
import os
from typing import Any

import requests

TIMEOUT = 120


class LLMError(RuntimeError):
    """Anything that came back wrong from a provider."""


class UnsupportedDocument(LLMError):
    """Provider cannot read a PDF; caller should fall back to plain text."""


# ---------------------------------------------------------------------------


class Provider:
    name = "base"
    required_env: str | None = None

    def preflight(self) -> None:
        """Fail before the first call, not on batch 1 of 40.

        Without this a missing key surfaces as a per-batch warning, every batch
        fails the same way, and the run ends with an empty digest that reads as
        "no good jobs today".
        """
        if self.required_env:
            self._env(self.required_env)

    def complete(self, model: str, system: str, user: str, max_tokens: int,
                 json_mode: bool = False) -> str:
        """`json_mode` asks the provider to guarantee valid JSON where it can.

        Most providers have a native switch for this. It is a request, not a
        promise — llm.parse_json still has to cope with whatever comes back.
        """
        raise NotImplementedError

    def complete_document(self, model: str, prompt: str, pdf: bytes,
                          max_tokens: int) -> str:
        raise UnsupportedDocument(
            f"{self.name} cannot read PDFs here - pass a .txt/.md resume instead"
        )

    @staticmethod
    def _env(key: str) -> str:
        # An empty value is a missing value. A blank line in .env otherwise
        # sails past the check and fails much later as an opaque 401.
        value = (os.environ.get(key) or "").strip()
        if not value:
            raise LLMError(f"{key} is not set (see .env.example)")
        return value


class FallbackProvider(Provider):
    """Try configured providers in order until one returns usable output."""

    def __init__(self, candidates: list[tuple[Provider, str]]):
        if not candidates:
            raise ValueError("FallbackProvider needs at least one candidate")
        self.candidates = candidates
        self.name = candidates[0][0].name
        self._active_index = 0

    @staticmethod
    def _validate_json(raw: str) -> None:
        """Reject non-JSON replies here so a later provider can be attempted."""
        cleaned = (raw or "").strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1]
            if cleaned.endswith("```"):
                cleaned = cleaned[:-3].strip()
        try:
            json.loads(cleaned)
            return
        except (TypeError, ValueError):
            pass
        # Accommodate a short preamble or trailing note around the JSON value.
        for opener, closer in (("[", "]"), ("{", "}")):
            start, end = cleaned.find(opener), cleaned.rfind(closer)
            if start >= 0 and end > start:
                try:
                    json.loads(cleaned[start:end + 1])
                    return
                except (TypeError, ValueError):
                    continue
        raise LLMError("provider returned invalid JSON")

    def _try(self, operation: str, *args, validate_json: bool = False,
             **kwargs) -> str:
        failures: list[str] = []
        for index in range(self._active_index, len(self.candidates)):
            provider, model = self.candidates[index]
            try:
                raw = getattr(provider, operation)(model, *args, **kwargs)
                if validate_json:
                    self._validate_json(raw)
                if index:
                    print(f"  ! {failures[0]} — switched to {provider.name}/{model}")
                return raw
            except Exception as e:
                failures.append(f"{provider.name}/{model} failed ({type(e).__name__}: {e})")
                if index + 1 < len(self.candidates):
                    self._active_index = index + 1
        raise LLMError("all configured models failed: " + "; ".join(failures))

    def complete(self, model: str, system: str, user: str, max_tokens: int,
                 json_mode: bool = False) -> str:
        return self._try("complete", system, user, max_tokens,
                         validate_json=json_mode, json_mode=json_mode)

    def complete_document(self, model: str, prompt: str, pdf: bytes,
                          max_tokens: int) -> str:
        return self._try("complete_document", prompt, pdf, max_tokens,
                         validate_json=True)


class AnthropicProvider(Provider):
    """Claude via the official SDK."""

    name = "anthropic"
    required_env = "ANTHROPIC_API_KEY"

    def _client(self):
        try:
            from anthropic import Anthropic
        except ImportError:
            raise LLMError("pip install anthropic") from None
        return Anthropic(api_key=self._env("ANTHROPIC_API_KEY"))

    @staticmethod
    def _text(msg) -> str:
        return "".join(b.text for b in msg.content if getattr(b, "type", None) == "text")

    def complete(self, model: str, system: str, user: str, max_tokens: int,
                 json_mode: bool = False) -> str:
        # No native JSON switch needed here — the prompts already specify the
        # shape and Claude honours it. json_mode is accepted and ignored.
        msg = self._client().messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        return self._text(msg)

    def complete_document(self, model: str, prompt: str, pdf: bytes,
                          max_tokens: int) -> str:
        msg = self._client().messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": [
                {"type": "document", "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.b64encode(pdf).decode(),
                }},
                {"type": "text", "text": prompt},
            ]}],
        )
        return self._text(msg)


class GeminiProvider(Provider):
    """Google AI Studio REST API. Generous free tier, no card needed."""

    name = "gemini"
    required_env = "GEMINI_API_KEY"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def _post(self, model: str, body: dict) -> str:
        r = requests.post(
            f"{self.BASE}/{model}:generateContent",
            params={"key": self._env("GEMINI_API_KEY")},
            json=body,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise LLMError(f"gemini HTTP {r.status_code}: {r.text[:300]}")
        try:
            candidate = r.json()["candidates"][0]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"gemini returned no candidates: {r.text[:300]}") from e

        # Gemini 2.5+ spends output tokens on internal thinking, so a tight
        # maxOutputTokens gets consumed before the answer starts. That arrives
        # as truncated JSON or no parts at all — say so plainly instead of
        # letting it surface three layers up as a parse error.
        reason = candidate.get("finishReason")
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        if reason == "MAX_TOKENS" or (not text and reason not in (None, "STOP")):
            raise LLMError(
                f"gemini stopped early (finishReason={reason}) with "
                f"{len(text)} chars of output — raise max_tokens for this stage"
            )
        if not text:
            raise LLMError(f"gemini returned no text: {r.text[:300]}")
        return text

    def complete(self, model: str, system: str, user: str, max_tokens: int,
                 json_mode: bool = False) -> str:
        gen: dict[str, Any] = {"maxOutputTokens": max_tokens, "temperature": 0.2}
        if json_mode:
            gen["responseMimeType"] = "application/json"
        body: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": gen,
        }
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        return self._post(model, body)

    def complete_document(self, model: str, prompt: str, pdf: bytes,
                          max_tokens: int) -> str:
        return self._post(model, {
            "contents": [{"role": "user", "parts": [
                {"inline_data": {"mime_type": "application/pdf",
                                 "data": base64.b64encode(pdf).decode()}},
                {"text": prompt},
            ]}],
            "generationConfig": {"maxOutputTokens": max_tokens, "temperature": 0.2},
        })


class OpenAICompatProvider(Provider):
    """Anything speaking /chat/completions - Groq, Together, OpenRouter, vLLM."""

    name = "openai-compatible"
    required_env = "OPENAI_API_KEY"
    default_base = "https://api.groq.com/openai/v1"
    key_env = "OPENAI_API_KEY"

    def complete(self, model: str, system: str, user: str, max_tokens: int,
                 json_mode: bool = False) -> str:
        return self._chat_completion(model, system, user, max_tokens, json_mode)

    def _chat_completion(self, model: str, system: str, user: str,
                         max_tokens: int, json_mode: bool = False) -> str:
        base = os.getenv("LLM_BASE_URL", self.default_base).rstrip("/")
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": user}]
        payload: dict[str, Any] = {"model": model, "messages": messages,
                                   "max_tokens": max_tokens, "temperature": 0.2}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        r = requests.post(
            f"{base}/chat/completions",
            headers={"Authorization": f"Bearer {self._env(self.key_env)}"},
            json=payload,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            raise LLMError(f"{self.name} HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as e:
            raise LLMError(f"{self.name} malformed reply: {r.text[:300]}") from e

class GroqProvider(OpenAICompatProvider):
    name = "groq"
    required_env = "GROQ_API_KEY"
    key_env = "GROQ_API_KEY"
    default_base = "https://api.groq.com/openai/v1"


class MistralProvider(OpenAICompatProvider):
    name = "mistral"
    required_env = "MISTRAL_API_KEY"
    key_env = "MISTRAL_API_KEY"
    default_base = "https://api.mistral.ai/v1"


class CerebrasProvider(OpenAICompatProvider):
    name = "cerebras"
    required_env = "CEREBRAS_API_KEY"
    key_env = "CEREBRAS_API_KEY"
    default_base = "https://api.cerebras.ai/v1"


class SambaNovaProvider(OpenAICompatProvider):
    name = "sambanova"
    required_env = "SAMBANOVA_API_KEY"
    key_env = "SAMBANOVA_API_KEY"
    default_base = "https://api.sambanova.ai/v1"


class OllamaProvider(Provider):
    """Fully local. No key, no cost, no rate limit - just a slower model."""

    name = "ollama"

    def complete(self, model: str, system: str, user: str, max_tokens: int,
                 json_mode: bool = False) -> str:
        base = os.getenv("OLLAMA_HOST", "http://localhost:11434").rstrip("/")
        messages = ([{"role": "system", "content": system}] if system else []) + \
                   [{"role": "user", "content": user}]
        payload: dict[str, Any] = {
            "model": model, "messages": messages, "stream": False,
            "options": {"temperature": 0.2, "num_predict": max_tokens},
        }
        if json_mode:
            payload["format"] = "json"
        try:
            r = requests.post(
                f"{base}/api/chat", json=payload, timeout=TIMEOUT,
            )
        except requests.RequestException as e:
            raise LLMError(
                f"ollama unreachable at {base} - is `ollama serve` running?") from e
        if r.status_code != 200:
            raise LLMError(f"ollama HTTP {r.status_code}: {r.text[:300]}")
        try:
            return r.json()["message"]["content"]
        except (KeyError, ValueError) as e:
            raise LLMError(f"ollama malformed reply: {r.text[:300]}") from e


PROVIDERS = {
    "anthropic": AnthropicProvider,
    "gemini": GeminiProvider,
    "groq": GroqProvider,
    "mistral": MistralProvider,
    "cerebras": CerebrasProvider,
    "sambanova": SambaNovaProvider,
    "openai-compatible": OpenAICompatProvider,
    "ollama": OllamaProvider,
}

# What each provider gets if you do not name a model, so `--scorer llm` works
# after setting nothing but a key.
DEFAULT_MODELS = {
    "anthropic": {"screen": "claude-haiku-4-5-20251001", "draft": "claude-sonnet-5",
                  "profile": "claude-sonnet-5"},
    "gemini": {"screen": "gemini-3.6-flash", "draft": "gemini-3.6-flash",
               "profile": "gemini-3.6-flash"},
    "groq": {"screen": "llama-3.3-70b-versatile", "draft": "llama-3.3-70b-versatile",
             "profile": "llama-3.3-70b-versatile"},
    "mistral": {"screen": "mistral-small-latest", "draft": "mistral-large-latest",
                "profile": "mistral-small-latest"},
    "cerebras": {"screen": "qwen-3.8-27b", "draft": "qwen-3.8-27b",
                 "profile": "qwen-3.8-27b"},
    "sambanova": {"screen": "Meta-Llama-3.3-70B-Instruct",
                  "draft": "Meta-Llama-3.3-70B-Instruct",
                  "profile": "Meta-Llama-3.3-70B-Instruct"},
    "openai-compatible": {"screen": "gpt-4o-mini", "draft": "gpt-4o",
                          "profile": "gpt-4o"},
    "ollama": {"screen": "llama3.1", "draft": "llama3.1", "profile": "llama3.1"},
}


def get_provider(name: str) -> Provider:
    try:
        return PROVIDERS[name]()
    except KeyError:
        raise LLMError(
            f"unknown provider {name!r}; pick one of {', '.join(PROVIDERS)}"
        ) from None


def resolve(stage: str, check: bool = True) -> tuple[Provider, str]:
    """Resolve the primary provider and its configured ordered fallbacks."""
    stage_key = stage.upper()
    primary_name = (os.getenv(f"{stage_key}_PROVIDER")
                    or os.getenv("LLM_PROVIDER")
                    or "anthropic").strip().lower()
    fallback_names = [name.strip().lower() for name in os.getenv(
        "LLM_FALLBACK_PROVIDERS", ""
    ).split(",") if name.strip()]
    names = list(dict.fromkeys([primary_name, *fallback_names]))

    candidates: list[tuple[Provider, str]] = []
    missing_keys: list[str] = []
    for index, name in enumerate(names):
        provider = get_provider(name)
        specific_model_key = f"{name.upper()}_{stage_key}_MODEL"
        if index == 0:
            model = (os.getenv(f"{stage_key}_MODEL") or "").strip()
        else:
            model = (os.getenv(specific_model_key) or "").strip()
        model = model or DEFAULT_MODELS.get(name, {}).get(stage)
        if not model:
            raise LLMError(f"set {specific_model_key} for provider {name!r}")
        try:
            if check:
                provider.preflight()
        except LLMError as e:
            if not fallback_names:
                raise
            missing_keys.append(str(e))
            continue
        candidates.append((provider, model))

    if not candidates:
        detail = "; ".join(missing_keys) or "no providers are configured"
        raise LLMError(f"no configured provider has an API key: {detail}")
    if len(candidates) == 1:
        return candidates[0]
    return FallbackProvider(candidates), candidates[0][1]
