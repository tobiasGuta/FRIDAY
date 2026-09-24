# FRIDAY development guardrails

1. Keep `src/friday/core` entirely independent of model SDKs and audio libraries.
2. Model tools are deny-by-default. Only explicitly registered read-only capabilities are model-callable. The clock is the sole built-in in v0.3.0; approval-gated tools remain unadvertised and non-executable. A model tool request is not authorization for broader actions.
3. Do not persist raw audio or log API keys. Never commit `.env`.
4. Add unit tests using fake providers before modifying the Gemini adapter.
5. Use `python -m pytest -q`, `python -m compileall -q src tests`, and `ruff check .` before committing.
6. Scope changes to one milestone. Add one Phase 1 capability at a time after this registry milestone; preserve v0.2.4 voice-turn behavior and the terminal prompt.
