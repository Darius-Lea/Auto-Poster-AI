"""Short storage waits for HTTP requests, preserving background worker waits."""


def busy_timeout_ms(default=15_000):
    # These factories are also used by the original publisher, whose dependency
    # set does not include Flask. It must remain usable without the web app.
    try:
        from flask import has_request_context
    except ImportError:
        return default
    return min(default, 2_000) if has_request_context() else default
