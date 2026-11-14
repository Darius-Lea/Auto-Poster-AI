"""Managed AI APIs with bounded, observable fallback and quota cooldowns.

Credentials belong to the application operator. Customer prompts never select
API URLs, supply provider secrets, or sign into consumer ChatGPT/Claude sessions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import os
import re
import threading
import time

import requests

PROVIDERS = ("openai", "anthropic", "gemini")
CONFIG = {
    "openai": ("OPENAI_API_KEY", "OPENAI_MODEL", "gpt-4.1-mini", "OpenAI"),
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "claude-sonnet-4-20250514", "Anthropic"),
    "gemini": ("GEMINI_API_KEY", "GEMINI_MODEL", "gemini-2.5-flash", "Google Gemini"),
}
_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,100}$")
_LOCK = threading.Lock()
_COOLDOWNS: dict[str, dict] = {}
_TIMEOUT = (5, 45)


class AIUnavailable(RuntimeError):
    """No configured provider could complete the request; safe metadata only."""

    def __init__(self, attempts, retry_after=None):
        super().__init__("AI services are temporarily unavailable. Please try again later.")
        self.attempts = attempts
        self.retry_after = retry_after


class AIRequestError(RuntimeError):
    """The request was rejected; changing providers must not evade the rejection."""

    def __init__(self, message="The AI service could not accept this request.", *, attempts=None):
        super().__init__(message)
        self.attempts = attempts or []


def _settings(provider):
    key_name, model_name, default_model, label = CONFIG[provider]
    key = os.getenv(key_name, "").strip()
    model = os.getenv(model_name, default_model).strip()
    fingerprint = hashlib.sha256((key + "\0" + model).encode()).hexdigest()
    return key, model, fingerprint, label


def _order(preferred=None):
    configured_order = os.getenv("AUTOPOST_AI_PROVIDERS", ",".join(PROVIDERS))
    order = []
    for name in configured_order.split(","):
        name = name.strip().lower()
        if name in PROVIDERS and name not in order:
            order.append(name)
    preferred = preferred or os.getenv("AUTOPOST_AI_PREFERRED", "").strip().lower()
    if preferred and preferred not in PROVIDERS:
        raise ValueError("Choose a supported AI service")
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    return order


def _cooldown(provider, fingerprint, now):
    with _LOCK:
        saved = _COOLDOWNS.get(provider)
        if saved and saved["fingerprint"] == fingerprint and saved["until"] > now:
            return saved.copy()
        _COOLDOWNS.pop(provider, None)
    return None


def _wait(provider, fingerprint, seconds, reason):
    with _LOCK:
        _COOLDOWNS[provider] = {
            "until": time.time() + seconds, "fingerprint": fingerprint, "reason": reason,
        }


def _retry_after(response, default=60):
    raw = response.headers.get("Retry-After", "")
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(raw)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            seconds = (target - datetime.now(timezone.utc)).total_seconds()
        except (TypeError, ValueError, OverflowError, IndexError):
            seconds = default
    # Discard NaN and infinity, and bound unreasonable upstream values.
    if not isinstance(seconds, (int, float)) or not 0 <= seconds <= 604800:
        seconds = default
    return max(1, int(seconds + .999))


def provider_status():
    """Safe operator/customer status; never returns API credentials or URLs."""
    order = _order()
    result = []
    now = time.time()
    for name in PROVIDERS:
        key, model, fingerprint, label = _settings(name)
        waiting = _cooldown(name, fingerprint, now) if key else None
        enabled = name in order and bool(key) and bool(_MODEL.fullmatch(model))
        result.append({
            "id": name, "name": label, "configured": enabled,
            "preferred": bool(order) and name == order[0],
            "state": "waiting" if enabled and waiting else "ready" if enabled else "unavailable",
            "retry_after_seconds": max(0, int(waiting["until"] - now + .999)) if waiting else 0,
        })
    return {"providers": result, "order": order,
            "notice": "AI connections are managed by this app. Consumer chat subscriptions are separate from API access."}


def _payload(provider, prompt, system, model, max_tokens):
    if provider == "openai":
        messages = ([{"role": "system", "content": system}] if system else [])
        messages.append({"role": "user", "content": prompt})
        return "https://api.openai.com/v1/chat/completions", {
            "model": model, "messages": messages, "max_tokens": max_tokens,
        }
    if provider == "anthropic":
        data = {"model": model, "messages": [{"role": "user", "content": prompt}],
                "max_tokens": max_tokens}
        if system:
            data["system"] = system
        return "https://api.anthropic.com/v1/messages", data
    data = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": max_tokens}}
    if system:
        data["systemInstruction"] = {"parts": [{"text": system}]}
    return f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent", data


def _text(provider, data):
    if not isinstance(data, dict):
        raise ValueError("Invalid AI response")
    if provider == "openai":
        choice = data["choices"][0]
        message = choice["message"]
        if message.get("refusal") or choice.get("finish_reason") == "content_filter":
            raise AIRequestError("The AI service declined this request.")
        text = message.get("content")
        finish = choice.get("finish_reason")
    elif provider == "anthropic":
        finish = data.get("stop_reason")
        if finish == "refusal":
            raise AIRequestError("The AI service declined this request.")
        text = "\n".join(block["text"] for block in data["content"]
                         if isinstance(block, dict) and block.get("type") == "text"
                         and isinstance(block.get("text"), str))
    else:
        if data.get("promptFeedback", {}).get("blockReason"):
            raise AIRequestError("The AI service declined this request.")
        choice = data["candidates"][0]
        finish = choice.get("finishReason")
        if finish in {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}:
            raise AIRequestError("The AI service declined this request.")
        text = "\n".join(part["text"] for part in choice["content"]["parts"]
                         if isinstance(part, dict) and isinstance(part.get("text"), str))
    if not isinstance(text, str) or not text.strip() or len(text) > 50000:
        raise ValueError("Invalid AI response")
    return text.strip(), finish


def generate_text(prompt: str, *, system: str = "", preferred: str | None = None,
                  max_tokens: int = 700) -> dict:
    """Try ranked configured providers, with failover only on availability errors.

    A provider whose cooldown expires returns to its normal rank automatically
    on the next call. Cooldowns are process-local and contain no prompt text.
    The function generates a draft only; it cannot publish or change schedules.
    """
    if not isinstance(prompt, str) or not 2 <= len(prompt.strip()) <= 20000:
        raise ValueError("Enter content between 2 and 20,000 characters")
    if not isinstance(system, str) or len(system) > 20000:
        raise ValueError("The writing instructions are too long")
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 4096:
        raise ValueError("Choose a valid AI response length")
    order = _order(preferred)
    attempts = []
    available_count = 0
    started = time.monotonic()
    for provider in order:
        key, model, fingerprint, _ = _settings(provider)
        if not key or not _MODEL.fullmatch(model):
            continue
        available_count += 1
        wait = _cooldown(provider, fingerprint, time.time())
        if wait:
            attempts.append({"provider": provider, "status": "skipped", "reason": wait["reason"],
                             "retry_after_seconds": max(1, int(wait["until"] - time.time() + .999)),
                             "latency_ms": 0})
            continue
        headers = {"Content-Type": "application/json"}
        if provider == "openai":
            headers["Authorization"] = "Bearer " + key
        elif provider == "anthropic":
            headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        else:
            headers["x-goog-api-key"] = key
        url, payload = _payload(provider, prompt.strip(), system, model, max_tokens)
        attempt_started = time.monotonic()
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=_TIMEOUT)
        except (requests.Timeout, requests.ConnectionError):
            _wait(provider, fingerprint, 30, "connection_unavailable")
            attempts.append({"provider": provider, "status": "unavailable", "reason": "connection_unavailable",
                             "retry_after_seconds": 30,
                             "latency_ms": round((time.monotonic() - attempt_started) * 1000)})
            continue
        except requests.RequestException:
            # Do not reflect exception messages: they can contain credential URLs.
            attempts.append({"provider": provider, "status": "rejected", "reason": "request_error",
                             "latency_ms": round((time.monotonic() - attempt_started) * 1000)})
            raise AIRequestError(attempts=attempts) from None
        latency = round((time.monotonic() - attempt_started) * 1000)
        status = response.status_code
        if status == 429 or 500 <= status <= 599 or status in (408, 425):
            delay = _retry_after(response, default=60 if status == 429 else 30)
            reason = "usage_limit" if status == 429 else "service_unavailable"
            _wait(provider, fingerprint, delay, reason)
            attempts.append({"provider": provider, "status": "unavailable", "reason": reason,
                             "retry_after_seconds": delay, "latency_ms": latency})
            continue
        if not 200 <= status <= 299:
            reason = "connection_needs_attention" if status in (401, 403) else "request_rejected"
            attempts.append({"provider": provider, "status": "rejected", "reason": reason,
                             "latency_ms": latency})
            raise AIRequestError(attempts=attempts)
        try:
            text, finish = _text(provider, response.json())
        except AIRequestError:
            attempts.append({"provider": provider, "status": "rejected", "reason": "declined",
                             "latency_ms": latency})
            raise AIRequestError("The AI service declined this request.", attempts=attempts) from None
        except (ValueError, KeyError, IndexError, TypeError):
            _wait(provider, fingerprint, 30, "invalid_response")
            attempts.append({"provider": provider, "status": "unavailable", "reason": "invalid_response",
                             "retry_after_seconds": 30, "latency_ms": latency})
            continue
        with _LOCK:
            _COOLDOWNS.pop(provider, None)
        attempts.append({"provider": provider, "status": "completed", "latency_ms": latency})
        return {"text": text, "provider": provider, "model": model,
                "fallback": len(attempts) > 1, "attempts": attempts,
                "finish_reason": finish,
                "latency_ms": round((time.monotonic() - started) * 1000)}
    retry = min((a["retry_after_seconds"] for a in attempts if a.get("retry_after_seconds")), default=None)
    if not available_count:
        raise AIUnavailable([], retry_after=None)
    raise AIUnavailable(attempts, retry_after=retry)
