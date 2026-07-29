# Cassettes

Recorded LLM responses, keyed by a SHA-256 prefix of the prompt.

`BANTAY_AI=replay` resolves AI proposals from this directory instead of calling a
model, so any code path that depends on an LLM stays deterministic and reviewable
in CI.

This directory is currently empty, and that is the expected state: the
deterministic scorer resolves or cleanly refuses every case in the present corpus,
so the `ASSISTED` tier is never reached. The resilience report shows this as an AI
escalation rate of 0.0%. Cassettes appear here only once a case actually escalates.

Record new ones with:

```bash
BANTAY_AI=record ANTHROPIC_API_KEY=... make gym
```

Each file stores the selector, the confidence, the model's stated reason and the
full prompt that produced it, so a reviewer can see what the model was asked
rather than only what it answered.
