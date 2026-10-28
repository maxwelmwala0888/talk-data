"""
LLM factory — picks the active provider AND wraps it in an auto-fallback chain.

Usage:
    from app.services.llm.llm_factory import get_llm
    llm = get_llm()              # returns a FallbackLLM wrapper
    llm.generate(prompt, system_instruction="...")

Fallback behaviour:
    - Tries the primary provider first
    - On rate-limit / timeout / overload errors, silently retries with the next
    - Non-retryable errors (bad prompt, auth) bubble up immediately
"""

from app.core.config import settings


def _retryable(err: Exception) -> bool:
    """Return True if we should try the next provider on this error."""
    msg = str(err).lower()
    retry_markers = [
        "quota", "429", "rate limit", "resource_exhausted",
        "too many requests", "overloaded", "503", "service unavailable",
        "timeout", "timed out", "connection", "reset", "econnreset",
    ]
    return any(k in msg for k in retry_markers)


def _build_service(name: str):
    """Return an LLM service instance for the given provider name, or None."""
    name = (name or "").lower().strip()

    try:
        if name == "openrouter":
            if not settings.OPENROUTER_API_KEY:
                return None
            from app.services.llm.openrouter_service import openrouter_service
            return openrouter_service

        if name == "gemini":
            if not settings.GEMINI_API_KEY:
                return None
            from app.services.llm.gemini_service import gemini_service
            return gemini_service

        if name == "groq":
            if not settings.GROQ_API_KEY:
                return None
            from app.services.llm.groq_service import groq_service
            return groq_service

        if name == "grok":
            if not settings.XAI_API_KEY:
                return None
            from app.services.llm.grok_service import grok_service
            return grok_service

        if name == "cerebras":
            if not getattr(settings, "CEREBRAS_API_KEY", ""):
                return None
            from app.services.llm.cerebras_service import cerebras_service
            return cerebras_service
    except Exception as e:
        print(f"[LLM] failed to load '{name}': {e}")
        return None

    return None


class FallbackLLM:
    """Wraps several providers and tries them in order on retryable errors."""

    def __init__(self, providers):
        # providers: list of (name, service_instance)
        self.providers = [(n, s) for n, s in providers if s is not None]

    def _try_each(self, method: str, *args, **kwargs):
        if not self.providers:
            raise RuntimeError("No LLM providers configured")

        last_err = None
        for i, (name, svc) in enumerate(self.providers):
            try:
                if i > 0:
                    print(f"[LLM] fallback → {name}")
                return getattr(svc, method)(*args, **kwargs)
            except Exception as e:
                last_err = e
                if not _retryable(e):
                    print(f"[LLM] {name} failed (non-retryable): {e}")
                    raise
                print(f"[LLM] {name} failed (retryable): {str(e)[:120]}")
                continue

        raise last_err or RuntimeError("All LLM providers failed")

    def generate(self, prompt: str, system_instruction: str = None, temperature: float = 0.0) -> str:
        return self._try_each("generate", prompt, system_instruction=system_instruction, temperature=temperature)

    def generate_json(self, prompt: str, system_instruction: str = None) -> str:
        return self._try_each("generate_json", prompt, system_instruction=system_instruction)


def get_llm():
    """Build the fallback chain from config and return a FallbackLLM wrapper."""
    primary = (settings.LLM_PROVIDER or "gemini").lower().strip()

    chain_raw = (settings.LLM_FALLBACK_CHAIN or "").strip()
    fallbacks = [p.strip().lower() for p in chain_raw.split(",") if p.strip()]

    # Build a deduped, ordered list — primary first, then fallbacks
    ordered = [primary]
    for name in fallbacks:
        if name and name != primary and name not in ordered:
            ordered.append(name)

    # Always include gemini at the very end as a safety net if configured
    if "gemini" not in ordered and settings.GEMINI_API_KEY:
        ordered.append("gemini")

    built = []
    for name in ordered:
        svc = _build_service(name)
        if svc is not None:
            built.append((name, svc))

    if not built:
        raise RuntimeError(
            "No LLM providers available. Set at least one API key in .env "
            "(GEMINI_API_KEY, OPENROUTER_API_KEY, GROQ_API_KEY)."
        )

    names = [n for n, _ in built]
    print(f"[LLM] chain = {' → '.join(names)}")
    return FallbackLLM(built)