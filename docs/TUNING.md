# Tuning

Every constant in `bantay/scoring.py` has a measurement behind it. This document
records how to re-derive them and what each one costs.

```bash
make tune          # writes reports/tuning/{corpus,sweep}.json and SWEEP.md
```

Runs offline: no browser, no network, no API key, about one second.

## Method

`bantay/gym/offline.py` builds a corpus in two parts:

* **targets** — one fingerprint per marked element, parsed from the clean fixture.
* **cases** — one mutated candidate set per seed, with an xpath-to-ground-truth map
  held outside the snapshots.

For each (target, case) pair it runs the scorer and classifies the outcome against
ground truth:

| outcome | meaning |
| --- | --- |
| correct | matched, and landed on the marked element |
| false heal | matched, but landed on something else |
| ambiguous | top two candidates too close; refused to choose |
| no match | best score below threshold; refused |

`recovery_rate` counts correct matches and `false_heal_rate` counts wrong ones.
Reporting recovery alone is what allows a self-healing framework to look effective
while quietly accepting wrong elements, so the gym exits non-zero on any false heal.

## Why ground truth lives outside the resolver

`data-bantay-truth` is present on every target element in the fixture. Two
independent mechanisms keep the resolver from reading it:

1. It is absent from `STABLE_ATTRS`, so neither the browser JS extractor nor the
   offline parser harvests it.
2. `build_prompt()` strips any `data-bantay*` key before anything reaches a model.

Both are asserted in `tests/unit/test_gym_contracts.py`. Mutation operators are also
required to preserve the markers: `mutate()` raises if they disappear, because a
mutation that destroys them produces a run whose numbers look plausible and carry no
information.

## Accept threshold

`ACCEPT_THRESHOLD = 0.45`

| threshold | recovery | false heals |
| --- | --- | --- |
| 0.30 | 93.1% | 0 |
| 0.35 | 90.9% | 0 |
| 0.40 | 88.8% | 0 |
| **0.45** | **85.0%** | 0 |
| 0.50 | 84.1% | 0 |
| 0.55 | 84.1% | 0 |
| 0.60 | 71.9% | 0 |
| 0.70 | 61.9% | 0 |
| 0.80 | 48.4% | 0 |

0.30 recovers more. I did not take it, for two reasons.

The 0.45 to 0.55 band is flat, and flatness is the useful property in a committed
constant: the value is insensitive to small errors in the scorer, and this scorer
does have small errors, being calibrated against one fixture by a parser with no
layout engine. A value chosen from a steep part of the curve is one that works only
under the conditions it was measured in.

Second, each recovery gained below 0.45 comes from weaker evidence. The trade is not
recovery for nothing; it is a clean, reviewable failure exchanged for a heal that a
human should probably have looked at.

## Ambiguity margin

`AMBIGUITY_MARGIN = 0.10`

Ablation at threshold 0.45, 320 trials:

| margin | recovery | false heals | ambiguous |
| --- | --- | --- | --- |
| 0.00 | 85.0% | 8 | 0 |
| 0.02 | 85.0% | 3 | 5 |
| 0.05 | 85.0% | 0 | 8 |
| **0.10** | **85.0%** | **0** | 8 |
| 0.20 | 76.9% | 0 | 34 |

A sample of what the unguarded scorer did:

```
target=apply_promo seed=15 landed_on=<unmarked decoy> score=1.00
target=apply_promo seed=38 landed_on=<unmarked decoy> score=0.95
target=place_order seed=7  landed_on=<unmarked decoy> score=0.875
```

The seed-15 case is the significant one. A score of 1.00 means the decoy was
indistinguishable from the target under every signal the scorer has. No threshold
rejects a perfect score, so raising the confidence bar cannot address this class of
failure at any setting; only the relative comparison can.

0.05 is the smallest margin that cleared all eight. 0.10 is committed for headroom
and costs nothing, since recovery is identical at 0.00, 0.05 and 0.10. Above roughly
0.15 the guard begins refusing legitimate matches, which is where the real ceiling
sits.

`tests/unit/test_scoring.py` pins both constants, so neither can be changed without
someone re-running this sweep.

## The margin result holds at every threshold

Running the full grid (thresholds 0.30 to 0.80 against margins 0.00 to 0.20) makes
the point more clearly than the single-threshold ablation. With `margin = 0.00`,
false heals appear at every threshold in the range, always with a worst-case score
of 1.00:

```
threshold 0.30  margin 0.00  ->  8 false heals (worst score 1.00)
threshold 0.45  margin 0.00  ->  8 false heals (worst score 1.00)
threshold 0.65  margin 0.00  ->  6 false heals (worst score 1.00)
threshold 0.80  margin 0.00  ->  6 false heals (worst score 1.00)
```

Raising the confidence bar reduces recovery from 93% to 48% and removes none of
these failures. They are not confidence failures but discrimination failures, and
confidence is the wrong instrument for them. Any margin of 0.05 or above removes all
of them at no cost to recovery.

## The same argument one tier earlier

The margin only guards the scorer, at tiers `SCORED` and `ASSISTED`. Tiers `DIRECT`
and `FALLBACK` return before the scorer is consulted, so a declared strategy that
matches several elements would resolve on whichever came first in document order.

The `inject_decoys` operator places its decoy immediately before the real element
and leaves it enabled in the DOM, so document order and the visibility filter both
favour the wrong one. In the browser gym this produced 4 false heals, two of them at
tier `DIRECT`, where nothing in the log marked them as recoveries at all.

`_find()` therefore refuses a strategy that matches more than one candidate rather
than taking the first hit, and the walk continues to the next strategy. When every
declared strategy is ambiguous, the decision reaches the scorer and the margin
applies as normal. This removed all 4, converting them into clean failures:
false-heal rate 2.4% to 0.0%, with recovery falling from 97.7% to 88.9% because the
four recoveries that disappeared had been landing on the wrong element.

## Weights

Set by hand from one observation: the attributes that break during a redesign are
usually not the ones that carry meaning. Class names and generated ids churn
constantly, while accessible names, roles and visible copy change rarely because
they are what users read.

| signal | weight | rationale |
| --- | --- | --- |
| accessible name | 0.30 | changes only when the product's meaning changes |
| stable attributes | 0.25 | `data-test`, `name`, `role`, `type` weighted 2x within this bucket |
| structure | 0.20 | ancestor-path suffix, so markup near the root costs little |
| text | 0.10 | strong, but often duplicated across elements |
| classes | 0.10 | kept low; class churn is a main cause of rot |
| sibling position | 0.05 | weakest signal, kept only as a tiebreaker |

These should be fitted rather than hand-set. Doing that properly needs a labelled
corpus across many real sites, which I did not have. Hand-set weights with a stated
rationale and a harness able to detect when they are wrong seemed preferable to
fitted weights derived from a single page.
