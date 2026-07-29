# ADR 0004 — Split the gym into browser and browserless halves

**Status:** accepted

## Context

Tuning `ACCEPT_THRESHOLD` and `AMBIGUITY_MARGIN` requires hundreds of scorer runs
across many seeds. Through a browser that is minutes per sweep and requires Chrome,
so in practice it would not be run often, and a constant that is expensive to
re-derive tends to stop matching the code.

## Decision

Two paths to the same scorer:

* `gym/run.py` — real Chrome, authoritative, requires a browser.
* `gym/offline.py` — stdlib `html.parser`, about one second, runs in CI, used for
  tuning.

## Rationale

Making the sweep cheap is what makes it routine. `make tune` regenerates every number
in the README in a second, so a reviewer can falsify the claims and a contributor
cannot invalidate them without noticing.

The cost is a second, approximate implementation of DOM extraction. Accepted because
the alternative is constants chosen by feel, and because the divergence is bounded
and stated: no layout engine, therefore no visibility computation, and only a subset
of the accessible-name algorithm.

## Consequences

- Reported in the README under limitations rather than left implicit.
- The two extractors can drift. Mitigated by keeping `STABLE_ATTRS` and the snapshot
  model in `dom.py` as the single shared contract.
- The parser exposed a bug in itself during development: labels do not carry an `id`
  of their own, so indexing them through the id map produced empty accessible names
  and cost roughly 0.4 of score on every input field. Fixed, and the reason the
  corpus is now checked against the hand-written registry.
