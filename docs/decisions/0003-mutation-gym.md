# ADR 0003 — Measure resilience with fault injection

**Status:** accepted

## Context

Any framework can claim resilience. Claims about test infrastructure are unusually
hard to falsify, because the infrastructure is what you would normally use to check.

## Decision

Build a mutation gym: a local server that corrupts a fixture's DOM from a seed, plus
a harness that measures recovery rate and, via ground-truth markers held outside the
resolver, false-heal rate.

## Rationale

Adapted from mutation testing, which asks whether the tests notice when the code is
broken. The analogue here is whether the resolver copes when the page is broken, and
whether it copes correctly.

The second half is the part that needed designing. Recovery rate on its own is
misleading: a resolver that accepts any similar-looking element scores well on it
while reducing the suite to noise. Distinguishing a correct heal from a confident
wrong one requires something outside the resolver to know the answer, hence
`data-bantay-truth` and the isolation contract asserted in
`tests/unit/test_gym_contracts.py`.

The harness earned its place immediately. The margin ablation, which cannot be run
without it, found eight false heals including one scoring 1.00, and showed that the
fix cost nothing.

## Alternatives considered

- **Test against real site redesigns.** Highest fidelity, but redesigns are rare and
  not reproducible, so the sample size would be close to zero.
- **Hand-written broken fixtures.** No seed control, so coverage is limited to
  whatever the author thought of, which is the bias the harness exists to remove.

## Consequences

- Every resilience claim in the README is reproducible with `make tune`.
- The gym is itself tested, because an untested instrument produces figures that
  cannot be relied on.
- The offline parser duplicates part of the browser extractor's logic. Accepted cost,
  documented in ADR 0004.
