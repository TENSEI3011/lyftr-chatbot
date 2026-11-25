"""LiteLLM gateway: key pool with automatic failover + LiteLLM's built-in response cache."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import litellm
from litellm.caching.caching import Cache

from .config import Settings

log = logging.getLogger("llm")
litellm.suppress_debug_info = True
litellm.set_verbose = False

SIMULATABLE_STATUSES = {429, 500, 502, 503}


class AllProvidersUnavailable(Exception):
    """Raised when every key in the pool failed for this request."""


@dataclass
class LLMResult:
    reply: str
    resolution_source: str  # LIVE_INFERENCE | LLM_CACHE
    model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
    key_id: str | None
    fallback_attempts: int

    def metadata(self) -> dict:
        return {
            "resolution_source": self.resolution_source,
            "model": self.model,
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "key_id": self.key_id,
            "fallback_attempts": self.fallback_attempts,
        }


def parse_simulation_header(value: str | None, key_ids: list[str]) -> dict[str, int]:
    """Parse `key_1=429,key_2=500` / `*=429` into {key_id: status}. Invalid pairs are ignored."""
    if not value:
        return {}
    out: dict[str, int] = {}
    for pair in value.split(","):
        if "=" not in pair:
            continue
        k, _, v = pair.partition("=")
        k, v = k.strip(), v.strip()
        try:
            status = int(v)
        except ValueError:
            continue
        if status not in SIMULATABLE_STATUSES:
            continue
        targets = key_ids if k == "*" else [k]
        for t in targets:
            out[t] = status
    return out


def simulated_error(status: int, model: str, key_id: str) -> Exception:
    """Build the same LiteLLM exception type a real provider failure would raise."""
    msg = f"Simulated HTTP {status} for {key_id} (X-Simulate-Failure)"
    provider = model.split("/", 1)[0] if "/" in model else "openai"
    if status == 429:
        return litellm.RateLimitError(message=msg, llm_provider=provider, model=model)
    if status == 503:
        return litellm.ServiceUnavailableError(message=msg, llm_provider=provider, model=model)
    err = litellm.InternalServerError(message=msg, llm_provider=provider, model=model)
    err.status_code = status  # 500 or 502
    return err


class LLMGateway:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model
        # Key ids are positional: key_1, key_2, ... The raw key never leaves this object.
        self.keys: list[tuple[str, str]] = [
            (f"key_{i}", key) for i, key in enumerate(settings.llm_api_keys, start=1)
        ]
        self.cache_enabled = self._enable_cache()
        self.last_error: str | None = None  # redacted summary of the latest real provider failure

    def _enable_cache(self) -> bool:
        try:
            if self.settings.cache_type == "disk":
                litellm.cache = Cache(type="disk", disk_cache_dir=self.settings.cache_dir)
            else:
                litellm.cache = Cache(type="local")
            return True
        except Exception:  # pragma: no cover - fall back to in-memory
            log.exception("disk cache unavailable, using in-memory cache")
            litellm.cache = Cache(type="local")
            return True

    @property
    def key_ids(self) -> list[str]:
        return [kid for kid, _ in self.keys]

    async def complete(self, messages: list[dict], simulate: dict[str, int] | None = None) -> LLMResult:
        simulate = simulate or {}
        start = time.perf_counter()
        failures = 0
        for key_id, api_key in self.keys:
            try:
                if key_id in simulate:
                    raise simulated_error(simulate[key_id], self.model, key_id)
                kwargs = dict(
                    model=self.model,
                    messages=messages,
                    api_key=api_key,
                    caching=True,       # LiteLLM cache: key = model + full message list
                    num_retries=0,      # we own failover across keys
                    timeout=self.settings.llm_timeout_s,
                )
                if self.settings.mock_llm:
                    kwargs["mock_response"] = "(mock) You said: " + messages[-1]["content"]
                response = await litellm.acompletion(**kwargs)
                reply = (response.choices[0].message.content or "").strip()
                if not reply:
                    raise ValueError("empty completion")
            except Exception as exc:  # 429 / quota / 5xx / timeouts / bad key -> next key
                failures += 1
                detail = str(exc)
                for _, secret in self.keys:  # never log a raw key
                    detail = detail.replace(secret, "***")
                if key_id not in simulate:
                    self.last_error = f"{key_id}: {type(exc).__name__} status={getattr(exc, 'status_code', '')} {detail[:200]}"
                log.warning("key %s failed for model %s (%s, status=%s): %s", key_id, self.model,
                            type(exc).__name__, getattr(exc, "status_code", ""), detail[:300])
                continue

            latency_ms = int(round((time.perf_counter() - start) * 1000))
            cache_hit = bool((getattr(response, "_hidden_params", None) or {}).get("cache_hit"))
            usage = getattr(response, "usage", None)
            return LLMResult(
                reply=reply,
                resolution_source="LLM_CACHE" if cache_hit else "LIVE_INFERENCE",
                model=self.model,
                latency_ms=latency_ms,
                input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
                output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
                key_id=None if cache_hit else key_id,
                fallback_attempts=0 if cache_hit else failures,
            )
        raise AllProvidersUnavailable()
