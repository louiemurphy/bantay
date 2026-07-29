# Bantay

A locator-resilience layer for Robot Framework, Selenium and Python.

"Bantay" is Filipino for *watchman*. The framework recovers from broken element
locators deterministically, grades every recovery, and reports it, so that a suite
which is drifting away from the product says so instead of going quietly green.

A language model can propose a candidate selector, but it never decides a pass or
fail outcome. Every proposal is re-checked by the same deterministic scorer as
everything else and discarded if it does not hold up.

## The problem

Locator rot is what breaks most UI suites. A CSS framework is swapped, a bundler
regenerates ids, a `<button>` becomes an `<a role="button">`, and dozens of tests
fail without a single defect having been introduced.

The common answer is self-healing: when a selector fails, match whatever element
looks most similar. That introduces a worse failure mode. A test that heals onto
the wrong element passes while no longer exercising the product, which converts a
visible failure into an invisible one.

Bantay's position is that healing is only safe if it is measured, graded and
reported. The design follows from that, and so does the measurement harness used
to check it.

## How resolution works

Four tiers, increasing in cost and decreasing in trust.

| tier | meaning |
| --- | --- |
| `DIRECT` | A declared locator worked. The common case. |
| `FALLBACK` | A later declared strategy worked. The registry is drifting. |
| `SCORED` | No declared strategy worked; fingerprint scoring found an unambiguous match. |
| `ASSISTED` | Scoring found nothing; an LLM proposed a selector, which then had to pass the same scoring. |
| `FAILED` | Every tier was exhausted. Reported with the closest near miss and its score breakdown. |

Tiers 2 to 4 are recoveries rather than successes. They keep a run alive so it
produces a complete report instead of stopping at the first stale locator, and each
one is tagged, counted, and written to `reports/patches/` as a proposed diff for
review. The registry is never rewritten at runtime, so a change to test data is
always a reviewable event.

Two rules prevent a wrong element from being accepted:

- **A declared strategy that matches more than one element is refused.** Taking the
  first match would let document order decide which element a test acts on. When
  every declared strategy is ambiguous, the decision falls through to the scorer.
- **The scorer refuses when the top two candidates are close.** Details below.

Tests can assert on the tier, which is the mechanism that keeps drift visible:

```robotframework
Resolution Tier Should Be    checkout.email    DIRECT
No Locator Drift Should Have Occurred          # suite teardown
```

## Results

Every figure below is reproducible with `make tune`, which runs offline in about a
second with no browser and no API key.

### Recovery under DOM corruption

320 trials: 8 target elements against 40 mutation seeds, each seed applying 1 to 3
mutation operators (class renames, id scrambling and stripping, structural
wrapping, sibling reordering, `<button>` to `<a role=button>`, test-hook deletion,
decoy injection).

| accept threshold | recovery | false heals | refused |
| --- | --- | --- | --- |
| 0.30 | 93.1% | 0 | 9 |
| 0.40 | 88.8% | 0 | 28 |
| **0.45** | **85.0%** | **0** | 40 |
| 0.50 | 84.1% | 0 | 43 |
| 0.60 | 71.9% | 0 | 84 |
| 0.80 | 48.4% | 0 | 159 |

0.45 is the committed default because 0.45 to 0.55 form a plateau, which is what a
committed constant wants: a small error in the scorer does not change the outcome.
A value chosen from a steep part of the curve works on the machine it was measured
on and not reliably elsewhere.

### Why the ambiguity guard matters more than the threshold

Ablating the ambiguity margin at a fixed threshold of 0.45:

| ambiguity margin | recovery | false heals | refused as ambiguous |
| --- | --- | --- | --- |
| 0.00 (no guard) | 85.0% | **8** | 0 |
| 0.02 | 85.0% | **3** | 5 |
| **0.05** | 85.0% | **0** | 8 |
| 0.10 (committed) | 85.0% | 0 | 8 |
| 0.20 | 76.9% | 0 | 34 |

One of those eight false heals scored exactly **1.00**. An injected decoy button was
indistinguishable from the real one by every similarity measure available.

That case rules out the intuitive fix: no confidence threshold can reject a perfect
score, so raising confidence cannot help. The sweep confirms it directly, with
false heals persisting at every threshold tested including 0.80, always with a
worst-case score of 1.00. The effective question is not "how good is the best
candidate" but "how much better is it than the runner-up". Comparing the top two
removed all eight at no cost to recovery rate.

The same reasoning applies one tier earlier, which is why a declared strategy that
matches more than one element is refused rather than resolved on its first hit. The
scorer's margin only guards tiers 3 and 4; without the uniqueness check, an
injected decoy is accepted at tier `DIRECT` and never reaches the guard designed to
catch it.

### In a real browser

`make gym` runs the same measurement through Chrome and Selenium: 168 trials across
21 seeds, 0.0% false heals, 88.9% recovery of the locators the mutations broke, and
a 3.0% clean-failure rate where the resolver refused rather than guessing.

The gap between 85.0% here and 88.9% there is expected. The offline parser is an
approximation used for tuning; the browser run is authoritative.

## AI integration and its boundaries

`bantay/ai.py` is the only module that can reach a model. Four modes, selected by
`BANTAY_AI`:

| mode | behaviour |
| --- | --- |
| `off` *(default)* | No calls. Fully deterministic, no credentials needed. |
| `replay` | Replays cassettes from `cassettes/`, keyed by a content hash of the prompt. Used by CI. |
| `record` | Live call, then writes a cassette. |
| `live` | Live calls only. |

The model is fenced out of four things:

1. It cannot report a verdict.
2. It cannot write to the registry.
3. Its proposal is re-scored deterministically and discarded if it fails.
4. It never sees `data-bantay-truth`, the gym's ground-truth marker, and
   `tests/unit/test_gym_contracts.py` asserts that in two independent places.

The fourth point is what makes the false-heal rate meaningful. Without ground-truth
isolation the framework would be grading its own work.

Note that `cassettes/` is currently empty, and that is the expected state: the
deterministic scorer resolves or cleanly refuses every case in the present corpus,
so the `ASSISTED` tier is never reached. The resilience report records this as an AI
escalation rate of 0.0%.

## Getting started

Requires Python 3.12+ and, for the browser suites, Chrome.

```bash
git clone <this repo> && cd bantay
make install          # virtualenv and dependencies
make test             # unit tests: no browser, no network, under a second
make tune             # regenerate every number in this README, offline
make gym              # full resilience run in a real browser (needs Chrome)
make resilience       # Robot resilience suite against the gym (needs Chrome)
make public           # suite against saucedemo.com (needs Chrome and network)
make lint             # robot --dryrun: validates that every keyword resolves
```

The Makefile assumes a POSIX shell and `python3`. On Windows, either override the
interpreter with `make PY=python test` or call the modules directly:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit -q
.\.venv\Scripts\python.exe -m bantay.gym.run --seeds 20
.\.venv\Scripts\python.exe -m robot --listener bantay.listener.BantayListener --outputdir reports/robot tests/gym/
```

`make lint` is worth running before any browser work. It validates that every
keyword in every suite actually resolves, without launching a browser, which catches
a misremembered SeleniumLibrary keyword in about a second rather than several
minutes into a run.

Reproducing a single row of the resilience report:

```bash
python -m bantay.gym.run --seeds 1 --seed-start 7 --no-headless
```

## Project layout

```
bantay/
├── bantay/
│   ├── dom.py          # snapshot model and browser-side JS extractor
│   ├── scoring.py      # deterministic similarity and ambiguity guard
│   ├── registry.py     # YAML locators, fingerprints, patch proposals
│   ├── resolver.py     # the four-tier escalation pipeline
│   ├── ai.py           # LLM interface, cassettes, and its constraints
│   ├── keywords.py     # Robot Framework keyword library
│   ├── listener.py     # listener v3: telemetry, screenshots, outcome tagging
│   └── gym/
│       ├── mutations.py  # seeded DOM mutation operators
│       ├── server.py     # serves the fixture, mutated per ?seed=
│       ├── offline.py    # browserless corpus and threshold sweep
│       └── run.py        # in-browser resilience measurement
├── resources/
│   ├── locators/       # registries: strategies and fingerprints, ordered by trust
│   └── keywords/       # Robot resource files
├── tests/
│   ├── unit/           # 56 tests, no browser, under a second
│   ├── gym/            # resilience suite, asserts on tiers
│   └── web/            # public-site suite (saucedemo.com)
└── docs/
    ├── TUNING.md       # how the constants were chosen
    └── decisions/      # ADRs, including what was rejected and why
```

Two test targets, for different reasons. The public site provides realism. A
third-party redesign would make every measurement in this repository
unreproducible, so measurement happens against a local fixture instead.

## Testing strategy

- **Unit tests** cover the scoring algorithm, the resolver's tier logic, registry
  error messages, and the gym's own integrity, with no browser or network. A test
  framework's decision logic warrants tests of its own, and logic only reachable
  through Selenium tends not to get them.
- **The gym suite** asserts on resolution tiers rather than only on whether an
  element was found, and includes a control test requiring that every locator
  resolve `DIRECT` on the unmutated page. If that fails, the registry has drifted
  from the fixture and no other figure in the report is valid.
- **The public suite** is weighted towards negative and edge cases: wrong password,
  locked-out account, empty credentials, whitespace-only username.
- **CI** runs two jobs. The offline job is the merge gate, since it needs no browser
  and does not flake. The browser job is non-blocking, because a flaky gate teaches
  reviewers to ignore a red build.

## Limitations

- **The offline parser is an approximation.** It has no layout engine, so it cannot
  know what is visually hidden, and it implements only the subset of the
  accessible-name specification the fixture exercises. It is a tuning instrument;
  figures from `make gym` in a real browser are authoritative.
- **One fixture, eight elements.** The scorer's ranking was never wrong on this
  corpus, which is why false heals appear only when the ambiguity guard is removed.
  On a larger, more heterogeneous corpus I would expect ranking errors as well, and
  the weights in `scoring.py` would need re-deriving.
- **Weights are hand-set, not learned.** With a labelled corpus they should be
  fitted. I did not have one, and inventing one would have made the numbers look
  better without making them more true.
- **`ASSISTED` is demonstrated as a mechanism**, not validated at scale. The
  guardrails around the model are testable; the reliability of its proposals is not
  something this corpus can measure.
- **No visual regression or accessibility suite.** Both were scoped out rather than
  half-built. See `docs/decisions/0005-scope.md`.

## Suggested reading order

1. `bantay/scoring.py` — the algorithm and the ambiguity guard.
2. `tests/unit/test_scoring.py::TestBestMatch::test_near_identical_decoy_is_refused_not_guessed`
   — the regression test for the finding above.
3. `bantay/resolver.py` — the four tiers, and where each guard applies.
4. `bantay/gym/mutations.py` — the fault injection and its ground-truth contract.
5. `docs/TUNING.md` — the numbers and how to re-derive them.
6. `docs/decisions/` — the trade-offs, including the rejected options.
