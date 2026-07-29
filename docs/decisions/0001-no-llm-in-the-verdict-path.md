# ADR 0001 — No LLM in the verdict path

**Status:** accepted

## Context

The exercise invited AI integration. The common implementations are an LLM deciding
whether a page looks correct, or an LLM resolving elements at runtime with the test
continuing on its answer.

## Decision

No model output may influence a pass/fail verdict. Models may only produce
artifacts — candidate selectors and patch proposals — and every artifact is
re-validated by the deterministic scorer before use.

## Rationale

A regression suite's product is a trustworthy verdict. Its value rests on the same
input yielding the same answer, so that a change in the answer is evidence about the
product. A non-deterministic component in the verdict path removes that property: a
red-to-green transition stops being informative, and so does green-to-red.

There is a second cost. An LLM asked whether a page looks right will usually say
yes, because pages usually do look right. A test that mostly says yes behaves much
like a test that always says yes, and the difference only becomes apparent when it
matters.

## Consequences

- The suite runs fully deterministically with `BANTAY_AI=off`, the default.
- The `ASSISTED` tier is reachable, but is reported as a recovery rather than a pass.
- AI features cannot be presented as "the AI found the bug". That is a real cost in
  a showcase project and was accepted deliberately.
