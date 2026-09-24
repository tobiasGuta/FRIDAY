# FRIDAY development guardrails

1. Keep `src/friday/core` entirely independent of model SDKs and audio libraries.
2. Model tools are deny-by-default. The only permitted capability is the read-only local clock in `src/friday/tools`. A model tool request is not authorization for broader actions.
3. Do not persist raw audio or log API keys. Never commit `.env`.
4. Add unit tests using fake providers before modifying the Gemini adapter.
5. Use `python -m pytest -q`, `python -m compileall -q src tests`, and `ruff check .` before committing.
6. Scope changes to one milestone. Next milestone: verify v0.2 hardware, device failure paths, and Live multi-turn behavior before adding wake word.
