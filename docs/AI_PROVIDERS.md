# Managed AI draft generation and automatic availability fallback

`ai_providers.py` is a backend-only router for OpenAI, Anthropic and Google Gemini text-generation APIs. It creates text; it cannot publish, change schedules, connect social accounts or invent account analytics. The application owns the API connections. End users should see an AI-service preference and connection availability, not developer credentials.

## Integration API

```python
from ai_providers import generate_text, provider_status, AIUnavailable, AIRequestError

result = generate_text(
    "Write a concise post from these verified details: ...",
    system="Follow the customer's approved writing instructions. Do not invent facts.",
    preferred="openai",  # optional; must also be enabled in operator order
    max_tokens=700,
)
draft = result["text"]
```

The result includes `provider`, `model`, `fallback`, `attempts`, `finish_reason` and observed `latency_ms`. Attempt metadata contains bounded service identifiers and reasons, not raw upstream error bodies, headers, credentials or exception strings. A caller must still apply its own authentication, authorization, request-rate limits and explicit publishing approval. Output must be rendered as text rather than trusted HTML.

`provider_status()` returns safe configured/preferred/waiting state and cooldown seconds. It never returns secrets. `AIUnavailable` includes safe `attempts` and optional `retry_after`; no completed output is fabricated. `AIRequestError` means a non-availability rejection occurred; it is not routed around by trying another model.

## Operator configuration

Set secrets in the server's protected environment settings. Customers do not supply these values in the application.

| Provider | Server secret | Model override | Default |
| --- | --- | --- | --- |
| OpenAI API | `OPENAI_API_KEY` | `OPENAI_MODEL` | `gpt-4.1-mini` |
| Anthropic API | `ANTHROPIC_API_KEY` | `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` |
| Google Gemini API | `GEMINI_API_KEY` | `GEMINI_MODEL` | `gemini-2.5-flash` |

`AUTOPOST_AI_PROVIDERS` is a comma-separated enabled preference order, defaulting to `openai,anthropic,gemini`. Omitted providers are disabled. `AUTOPOST_AI_PREFERRED` optionally promotes one enabled provider. Services without a key or with an invalid model identifier are skipped. Existing environment model overrides are retained; operators must select models that their account currently supports.

The code uses the official fixed API endpoints: OpenAI Chat Completions, Anthropic Messages (`anthropic-version: 2023-06-01`), and Gemini `generateContent`. Requests use a five-second connection timeout and a 45-second response timeout. Gemini credentials are sent in the `x-goog-api-key` header, not a query string. No configurable destination URL, browser session cookie, social password or consumer chat login is accepted.

## Fallback behavior

- HTTP 429, server errors, HTTP 408/425 and connection/timeouts try the next configured service.
- `Retry-After` seconds or HTTP dates are honored, with unreasonable/non-finite values rejected and a maximum seven-day bound. Defaults are 60 seconds for 429 and 30 seconds for other transient failures.
- A provider on cooldown is skipped. When its cooldown expires it returns to its configured rank on the next generation request, so the preferred provider is attempted again without a manual switch.
- Malformed or empty successful responses produce no substitute content; the router records an invalid-response attempt and tries the next available service.
- HTTP 400/401/403/404 and other non-transient errors stop the request. Explicit safety/refusal responses also stop fallback. A request must not evade a provider's refusal by rotating services.
- Cooldowns are in-memory, thread-protected and reset on process restart or changed key/model configuration. This module does not know consumer subscription usage, plan reset dates or guaranteed quotas. It learns availability only from actual API responses.

A timed-out request may still have been processed by its provider. API usage can be billable even if the response was lost. Apply service-level budgets and usage controls in the hosting application; this module does not purchase credits or provision provider accounts.

## Consumer subscriptions and Muse

ChatGPT and Claude consumer subscriptions do not automatically provide API credits or authorization for a third-party application. Do not display a consumer “Sign in with ChatGPT/Claude” control unless the provider actually supports and approves that delegated integration. This implementation uses the platform operator's separate API access, keeping backend setup out of the customer flow.

The requested Muse service is `muse.ai`, a video hosting/search platform. It is not a general text model in this provider ranking. A Muse integration requires its own supported account/API workflow and video permissions; this router does not claim to connect to it or read its analytics.

## Validation

`python -m unittest discover -s tests -p test_ai_providers.py -v` passed **11 fixture-only tests** covering success metadata, quota failover and cooldown recovery, transient/timeout fallback, fixed endpoint/header authentication, refusal/non-transient rejection, empty-response failover, all-services-unavailable behavior, disabled providers, input/model validation and bounded retry metadata. No live provider call, paid generation, consumer login or production credential was used. Live generation and the hosting server's outbound access remain separate release checks.
