"""LLM with automatic failover between providers (Gemini and Groq, both via their
OpenAI-compatible APIs).

Free tiers are the constraint: Groq allows 8K tokens/minute and does not discount the
repeated system prompt, so with a ~3.5K-token prompt it handles about two exchanges a
minute. Gemini's free tier is far higher. LLM_ORDER decides who goes first; when a
provider fails (rate limit, outage, network), the same request goes to the next one and
the failed provider is rested for a short while. If everyone fails, the agent apologises
out loud instead of going silent.
"""

import copy
import os
import time
from dataclasses import dataclass

from loguru import logger
from openai import APIConnectionError, APIStatusError, RateLimitError
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.services.openai.llm import OpenAILLMService

GROQ_URL = "https://api.groq.com/openai/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
HOLD_SECS = 30
# Gemini 3 rejects tool calls in the history that lack a "thought signature" (a Google-
# specific tag other providers never produce). This documented placeholder skips the check.
SKIP_SIGNATURE = {"google": {"thought_signature": "skip_thought_signature_validator"}}
APOLOGY = "Sorry, I'm having a little trouble right now. Could you say that again?"


@dataclass
class Provider:
    name: str
    api_key: str
    base_url: str
    model: str
    reasoning_effort: str | None = None

    @property
    def is_gemini(self) -> bool:
        return "googleapis" in self.base_url


def provider_from_env(name: str) -> Provider | None:
    """Build a provider from .env, or None if its key is missing."""
    if name == "groq":
        key = os.getenv("GROQ_API_KEY")
        effort = os.getenv("GROQ_REASONING_EFFORT", "low") or None
        return Provider("groq", key, GROQ_URL, os.getenv("GROQ_MODEL") or "openai/gpt-oss-120b", effort) if key else None
    if name == "gemini":
        key = os.getenv("GEMINI_API_KEY")
        return Provider("gemini", key, GEMINI_URL, os.getenv("GEMINI_MODEL") or "gemini-3.5-flash-lite") if key else None
    raise ValueError(f"Unknown LLM provider {name!r} in LLM_ORDER (use gemini, groq)")


def build_llm(system_instruction: str) -> "FallbackLLMService":
    order = [n.strip().lower() for n in os.getenv("LLM_ORDER", "gemini,groq").split(",") if n.strip()]
    providers = [p for p in (provider_from_env(n) for n in order) if p]
    if not providers:
        raise RuntimeError("No LLM configured: set GEMINI_API_KEY and/or GROQ_API_KEY in .env")
    logger.info("LLM order: " + " -> ".join(f"{p.name} ({p.model})" for p in providers))
    return FallbackLLMService(providers, system_instruction)


def _worth_retrying(e: Exception) -> bool:
    """Rate limits, network errors and 5xx: the provider is the problem, not the request."""
    if isinstance(e, (RateLimitError, APIConnectionError)):
        return True
    return isinstance(e, APIStatusError) and e.status_code >= 500


class FallbackLLMService(OpenAILLMService):
    """OpenAI-compatible LLM service that tries each provider in order."""

    def __init__(self, providers: list[Provider], system_instruction: str):
        primary = providers[0]
        super().__init__(
            api_key=primary.api_key,
            base_url=primary.base_url,
            settings=OpenAILLMService.Settings(
                model=primary.model, system_instruction=system_instruction
            ),
        )
        self._providers = providers
        self._clients = [self._client] + [
            self.create_client(api_key=p.api_key, base_url=p.base_url) for p in providers[1:]
        ]
        self._down_until = [0.0] * len(providers)

    async def get_chat_completions(self, context):
        adapter = self.get_llm_adapter()
        base = self.build_chat_completion_params(
            adapter.get_llm_invocation_params(
                context,
                system_instruction=self._settings.system_instruction,
                convert_developer_to_user=True,
            )
        )
        now = time.monotonic()
        candidates = [i for i in range(len(self._providers)) if now > self._down_until[i]] or [0]

        last_error: Exception | None = None
        for i in candidates:
            provider = self._providers[i]
            try:
                return await self._clients[i].chat.completions.create(
                    **self._params_for(provider, base)
                )
            except Exception as e:
                last_error = e
                if not _worth_retrying(e):
                    break
                logger.warning(f"{provider.name} failed ({type(e).__name__}); resting it {HOLD_SECS}s")
                self._down_until[i] = time.monotonic() + HOLD_SECS

        await self.push_frame(TTSSpeakFrame(APOLOGY))
        raise last_error  # type: ignore[misc]

    @staticmethod
    def _params_for(provider: Provider, base: dict) -> dict:
        params = dict(base)
        params["model"] = provider.model
        params.pop("reasoning_effort", None)
        if provider.reasoning_effort:
            params["reasoning_effort"] = provider.reasoning_effort
        if provider.is_gemini:
            # Copy so the placeholder never leaks into the shared context (Groq rejects it).
            params["messages"] = copy.deepcopy(base["messages"])
            for message in params["messages"]:
                if message.get("role") == "assistant":
                    for tool_call in message.get("tool_calls") or []:
                        tool_call.setdefault("extra_content", SKIP_SIGNATURE)
        return params
