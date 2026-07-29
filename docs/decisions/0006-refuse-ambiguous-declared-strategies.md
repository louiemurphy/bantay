# ADR 0006 — Refuse a declared strategy that matches more than one element

**Status:** accepted

## Context

`AMBIGUITY_MARGIN` (ADR 0003, `docs/TUNING.md`) guards the scorer, which runs at
tiers `SCORED` and `ASSISTED`. Tiers `DIRECT` and `FALLBACK` return before the scorer
is consulted, so a declared strategy that matched several elements resolved on
whichever appeared first in document order.

The `inject_decoys` operator was written to exercise the ambiguity guard, but it
never reached it. The operator clones a button into a near-identical copy placed
immediately before the real element, stripping only the ground-truth marker. Because
the decoy retains its `data-test` hook and is disabled rather than hidden, both
document order and the visibility filter favour it, so the primary declared strategy
matched the decoy and resolution succeeded at tier `DIRECT`.

The browser gym recorded 4 false heals from this, two of them at `DIRECT`, where
nothing in the log identified them as recoveries at all.

## Decision

`_find()` raises `AmbiguousStrategy` when a strategy matches more than one candidate
after visibility filtering, rather than returning the first hit. The strategy walk
continues to the next entry, and when every declared strategy is ambiguous the
decision reaches the scorer, where the margin applies as normal.

## Rationale

The reasoning behind the ambiguity margin does not depend on which tier is running.
If two candidates cannot be distinguished, resolving on either is a guess, and a
guess that happens to be settled by document order is not better than one settled by
a score. Applying the rule at both tiers makes the guarantee uniform.

Narrower fixes were rejected. Filtering `disabled` elements, or tightening the
registry to `button[type='button']:not([disabled])`, would make this fixture pass
without addressing the resolver, and a decoy encountered in a real application will
not always be disabled.

## Consequences

- False-heal rate in the browser gym: 2.4% to 0.0%.
- Recovery rate: 97.7% to 88.9%. The four recoveries that disappeared had been
  landing on the wrong element, so the lower figure is the accurate one.
- Clean-failure rate rose from 0.6% to 3.0%. Those cases now fail loudly instead of
  passing incorrectly.
- A registry entry whose strategies are individually non-unique will fall through to
  the scorer more often, which is slower. Acceptable: a non-unique selector is a
  defect in the registry, and the patch proposal names it.
