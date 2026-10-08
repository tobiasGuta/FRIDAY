# FRIDAY development guardrails

1. Keep `src/friday/core` entirely independent of model SDKs and audio libraries.
2. Model tools are deny-by-default. Only explicitly registered read-only capabilities are model-callable. The clock is always read-only; web search requires explicit --web and a configured backend. Approval-gated tools remain unadvertised and non-executable. A model tool request is not authorization for broader actions.
3. Do not persist raw audio or log API keys. Never commit `.env`.
4. Add unit tests using fake providers before modifying the Gemini adapter.
5. Use `python -m pytest -q`, `python -m compileall -q src tests`, and `ruff check .` before committing.
6. Scope changes to one milestone. Add one Phase 1 capability at a time after this registry milestone; preserve v0.2.4 voice-turn behavior and the terminal prompt.

7. The v0.6 agent preview must be separately invoked. Its model never receives
   shell or filesystem-write tools, chooses workspace/repo scope, or approves actions.
   Do not wire it into `talk` or `desktop` without an explicit gated milestone.
8. Keep planning decisions provider-neutral, bounded and covered by offline
   adversarial tests. Read-only GitHub HTTP access requires explicit network opt-in.
