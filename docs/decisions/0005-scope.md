# ADR 0005 — What was deliberately left out

**Status:** accepted

## Context

The exercise invites visual validation, NLP test generation and other AI
applications. The available time was roughly one day.

## Decision

Build one pillar to a defensible depth — locator resilience with measurement — and
cut the rest, documenting the cuts rather than shipping stubs.

## Rationale

Four half-built features demonstrate less than one finished one. The parts that are
difficult to fake are the ones that come from depth: the ablation study, the
ground-truth contract, the stated limitations.

## Cut, and why

**Visual regression.** Feasible, but doing it well means solving determinism first:
freezing animations, pinning fonts, stubbing clocks, masking dynamic regions. Doing
it badly means shipping a flaky suite, which would contradict the argument this
project makes. Not a one-day item.

**LLM test-case generation.** The design was worked out: feed the model `libdoc` JSON
so it can only compose keywords that exist, then gate every generated suite through
`robot --dryrun` before it can be committed. That gate is already in the repository as
`make lint`, and it earned its place by catching a call to `Location Should Not
Contain`, a keyword SeleniumLibrary does not have. The generator on top of it is the
next thing I would build.

**Accessibility invariants.** `axe-core` injected via Selenium, with violations as
deterministic assertions and an LLM used only to triage severity, never to decide.
Valuable, but orthogonal to the argument being made here.

**Learned scorer weights.** Requires a labelled corpus across many real sites.
Fitting weights on a single fixture would have produced better-looking numbers and
worse information.

## Consequences

- Narrower surface than the exercise invited.
- Every remaining claim is measured and reproducible.
