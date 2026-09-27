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
EMPTY_REPLY = "Sorry, could you say that again?"


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


class _SafeStream:
    """Wraps a streaming response with two guarantees Pipecat doesn't give us.

    1. Every tool-call delta carries an ``index``. OpenAI and Groq number parallel
       tool calls 0, 1, 2...; Gemini's OpenAI-compatible endpoint sends ``index=None``.
       Pipecat's parser uses the index to notice when a new call starts, and ``None``
       makes it register a phantom nameless call first, which poisons the history
       (both providers then reject it with 400). Each distinct id gets a sequential
       index instead.

    2. The agent is never silent. If the stream ends without a single word or tool
       call (Gemini does this occasionally), ``retry`` is asked for a stream from the
       next provider; if that is empty too, ``on_empty`` is awaited so the agent can
       say something instead of leaving the caller wondering if the line is dead.
    """

    def __init__(self, stream, retry=None, on_empty=None):
        self._stream = stream
        self._retry = retry
        self._on_empty = on_empty

    def __aiter__(self):
        return self._run()

    async def _run(self):
        produced = False
        async for chunk in self._normalised(self._stream):
            produced = produced or _has_content(chunk)
            yield chunk
        if produced:
            return
        if self._retry:
            logger.warning("LLM returned an empty response; retrying on the next provider")
            retry_stream = await self._retry()
            if retry_stream is not None:
                await self.close()
                self._stream = retry_stream
                async for chunk in self._normalised(retry_stream):
                    produced = produced or _has_content(chunk)
                    yield chunk
        if not produced and self._on_empty:
            logger.error("LLM returned an empty response from every provider")
            await self._on_empty()

    @staticmethod
    async def _normalised(stream):
        index_by_id: dict[str, int] = {}
        current = -1
        async for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            for tc in (delta.tool_calls if delta and delta.tool_calls else []):
                if tc.index is not None:
                    continue
                if tc.id:
                    if tc.id not in index_by_id:
                        index_by_id[tc.id] = len(index_by_id)
                    current = index_by_id[tc.id]
                elif tc.function and tc.function.name:
                    current += 1  # a new call with no id at all
                tc.index = max(current, 0)
            yield chunk

    async def close(self):
        if hasattr(self._stream, "close"):
            await self._stream.close()


def _has_content(chunk) -> bool:
    if not chunk.choices:
        return False
    delta = chunk.choices[0].delta
    return bool(delta and (delta.content or delta.tool_calls))


def _worth_retrying(e: Exception) -> bool:
    """Try the next provider unless the failure is our own credentials.

    Rate limits, network errors and 5xx are obviously the provider's problem. A 400 can
    also be provider-specific (e.g. Gemini's OpenAI-compatible layer rejecting a
    conversation shape that Groq accepts), so it is worth a second opinion too.
    Only 401/403 (bad key) is pointless to retry elsewhere.
    """
    if isinstance(e, (RateLimitError, APIConnectionError)):
        return True
    return isinstance(e, APIStatusError) and e.status_code not in (401, 403)


def _describe(messages: list[dict]) -> str:
    """Compact shape of a conversation for error logs: roles, tool calls, empties."""
    parts = []
    for m in messages:
        role = m.get("role", "?")
        if m.get("tool_calls"):
            role += f"+{len(m['tool_calls'])}calls"
        content = m.get("content")
        if content in (None, ""):
            role += "(empty)"
        parts.append(role)
    return " > ".join(parts)


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
        for pos, i in enumerate(candidates):
            provider = self._providers[i]
            try:
                stream = await self._clients[i].chat.completions.create(
                    **self._params_for(provider, base)
                )
                others = [self._providers[j] for j in candidates[pos + 1 :]]
                clients = [self._clients[j] for j in candidates[pos + 1 :]]

                async def retry(others=others, clients=clients):
                    for p, c in zip(others, clients):
                        try:
                            return await c.chat.completions.create(**self._params_for(p, base))
                        except Exception as e:  # noqa: BLE001 - best effort only
                            logger.warning(f"{p.name} retry failed ({type(e).__name__})")
                    return None

                async def on_empty():
                    await self.push_frame(TTSSpeakFrame(EMPTY_REPLY, append_to_context=False))

                return _SafeStream(stream, retry=retry if others else None, on_empty=on_empty)
            except Exception as e:
                last_error = e
                if isinstance(e, APIStatusError) and e.status_code == 400:
                    # Request rejected: log what we sent so the cause is diagnosable.
                    logger.error(
                        f"{provider.name} rejected the request: {str(e)[:600]}\n"
                        f"  conversation shape: {_describe(base['messages'])}"
                    )
                if not _worth_retrying(e):
                    break
                logger.warning(f"{provider.name} failed ({type(e).__name__}); trying next provider")
                if not (isinstance(e, APIStatusError) and e.status_code == 400):
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
