# ADR 0002 — Healing produces a patch, not a green test

**Status:** accepted

## Context

Self-healing frameworks typically resolve a stale locator at runtime, continue, and
report the test as passed. Some persist the new locator automatically.

## Decision

A healed resolution is reported as `SCORED` or `ASSISTED`, never `DIRECT`. The
registry is never modified at runtime. A patch proposal is written to
`reports/patches/` instead, containing the current strategies, the proposed
strategy, the observed fingerprint, the evidence that produced it, and a review
checklist.

## Rationale

The failure mode of automatic healing is not a wrong result but the absence of one.
Consider a "Place order" button that was removed and replaced by "Continue to
payment". A healing framework finds a sufficiently similar button, clicks it, and
reports green. The suite has stopped testing checkout, and no artifact anywhere
records that.

Locator rot and product change are indistinguishable from inside the resolver. Only
someone with product context can tell them apart, so the resolver's job is to
surface the ambiguity rather than resolve it. `No Locator Drift Should Have
Occurred` in a suite teardown is what makes it visible.

## Consequences

- Registry maintenance stays a reviewed, deliberate act.
- Runs remain useful under drift: the suite completes and reports everything instead
  of aborting at the first stale locator.
- Teams wanting fully hands-off healing are not served by this design, which is
  intentional.
