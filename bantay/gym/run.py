"""Gym runner: measure the resolver against seeded DOM corruption.

For every seed and every locator the runner records which tier resolved it, then
answers the one question the resolver cannot: did it land on the right element?
That answer comes from `data-bantay-truth`, read here in the harness and never in
the resolver.

The separation is what makes the headline figure meaningful. Recovery rate on its
own is misleading, because a framework that accepts any similar-looking element
scores well on it while reducing the suite to noise. The figure that matters is
the false-heal rate, and it can only be computed by something outside the resolver
that already knows the correct answer.

Usage:
    python -m bantay.gym.run --seeds 25
    python -m bantay.gym.run --seeds 1 --seed-start 7 --no-headless
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

from ..dom import ElementSnapshot
from ..registry import LocatorRegistry
from ..resolver import DIRECT, LocatorResolver, ElementUnresolvable
from .mutations import TRUTH_ATTR, plan_for_seed
from .server import GymServer

REGISTRY_PATH = Path("resources/locators/gym_checkout.yaml")
REPORT_DIR = Path("reports/gym")


@dataclass
class Trial:
    seed: int
    mutations: list[str]
    locator_key: str
    expected_truth: str
    tier: str
    strategy: str | None
    landed_on: str | None
    correct: bool
    note: str

    @property
    def recovered(self) -> bool:
        return self.tier not in (DIRECT, "FAILED")

    @property
    def false_heal(self) -> bool:
        """Resolved successfully, but onto the wrong element: a passing test that
        no longer exercises the product."""
        return self.tier != "FAILED" and not self.correct


def build_driver(headless: bool = True):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options

    options = Options()
    if headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1280,900")
    # Pin locale and disable smooth scrolling: determinism over realism in the gym.
    options.add_argument("--lang=en-US")
    return webdriver.Chrome(options=options)


def run_seed(resolver: LocatorResolver, driver, url: str, seed: int,
             registry: LocatorRegistry) -> list[Trial]:
    driver.get(url)
    mutations = [m.name for m in plan_for_seed(seed)] if seed else []
    trials: list[Trial] = []

    for locator in registry:
        expected = (locator.fingerprint.attrs.get(TRUTH_ATTR)
                    if locator.fingerprint else "") or locator.key.split(".")[-1]
        try:
            resolution = resolver.resolve(locator.key)
        except ElementUnresolvable as exc:
            trials.append(Trial(seed, mutations, locator.key, expected, "FAILED",
                                None, None, False, str(exc).splitlines()[0]))
            continue

        landed = None
        try:
            landed = resolution.element.get_attribute(TRUTH_ATTR)
        except Exception:
            pass

        trials.append(Trial(
            seed=seed, mutations=mutations, locator_key=locator.key,
            expected_truth=expected, tier=resolution.tier,
            strategy=resolution.strategy, landed_on=landed,
            correct=(landed == expected), note=resolution.notes,
        ))
    return trials


def summarise(trials: list[Trial]) -> dict:
    total = len(trials)
    if not total:
        return {"error": "no trials recorded"}

    broken = [t for t in trials if t.tier != DIRECT]
    recovered = [t for t in broken if t.tier != "FAILED"]
    false_heals = [t for t in trials if t.false_heal]

    by_severity: dict[str, Counter] = defaultdict(Counter)
    for trial in trials:
        for name in trial.mutations or ["none"]:
            by_severity[name][trial.tier] += 1

    return {
        "trials": total,
        "seeds": len({t.seed for t in trials}),
        "tier_counts": dict(Counter(t.tier for t in trials)),
        # Of the locators the mutations actually broke, how many were recovered.
        "recovery_rate": round(len(recovered) / len(broken), 4) if broken else None,
        # Target: zero. See docs/TUNING.md for why this is the primary metric.
        "false_heal_rate": round(len(false_heals) / total, 4),
        "false_heals": [
            {"seed": t.seed, "locator": t.locator_key, "expected": t.expected_truth,
             "landed_on": t.landed_on, "mutations": t.mutations, "tier": t.tier}
            for t in false_heals
        ],
        "clean_failure_rate": round(
            sum(1 for t in trials if t.tier == "FAILED") / total, 4
        ),
        "ai_escalation_rate": round(
            sum(1 for t in trials if t.tier == "ASSISTED") / total, 4
        ),
        "per_mutation": {k: dict(v) for k, v in sorted(by_severity.items())},
    }


def render_markdown(summary: dict) -> str:
    lines = [
        "# Resilience Report",
        "",
        f"- trials: **{summary['trials']}** across **{summary['seeds']}** seeds",
        f"- recovery rate (of locators the mutations broke): "
        f"**{_pct(summary['recovery_rate'])}**",
        f"- **false-heal rate: {_pct(summary['false_heal_rate'])}** "
        f"(resolved the wrong element - target 0%)",
        f"- clean failure rate: {_pct(summary['clean_failure_rate'])} "
        f"(refused to guess; correct behaviour when the product really changed)",
        f"- AI escalation rate: {_pct(summary['ai_escalation_rate'])} "
        f"(how often deterministic scoring was not enough)",
        "",
        "## Outcomes by tier",
        "",
        "| tier | count |",
        "| --- | --- |",
    ]
    for tier, count in sorted(summary["tier_counts"].items(), key=lambda kv: -kv[1]):
        lines.append(f"| {tier} | {count} |")

    lines += ["", "## Outcomes by mutation operator", "",
              "| mutation | " + " | ".join(sorted(summary["tier_counts"])) + " |",
              "| --- | " + " | ".join("---" for _ in summary["tier_counts"]) + " |"]
    for mutation, counts in summary["per_mutation"].items():
        row = [str(counts.get(tier, 0)) for tier in sorted(summary["tier_counts"])]
        lines.append(f"| `{mutation}` | " + " | ".join(row) + " |")

    if summary["false_heals"]:
        lines += ["", "## False heals", "",
                  "The tier identifies which stage accepted the wrong element: a "
                  "declared strategy at `DIRECT` or `FALLBACK`, or the scorer at "
                  "`SCORED` or `ASSISTED`.", ""]
        for item in summary["false_heals"]:
            lines.append(
                f"- seed `{item['seed']}` `{item['locator']}` at tier "
                f"`{item['tier']}`: expected `{item['expected']}`, landed on "
                f"`{item['landed_on']}` after `{', '.join(item['mutations'])}`"
            )
    else:
        lines += ["", "No false heals recorded. Every locator that could not be "
                  "recovered failed rather than resolving to the wrong element.", ""]
    return "\n".join(lines) + "\n"


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=20, help="number of seeds to run")
    parser.add_argument("--seed-start", type=int, default=1)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--out", type=Path, default=REPORT_DIR)
    parser.add_argument("--no-headless", action="store_true")
    args = parser.parse_args(argv)

    registry = LocatorRegistry.load(args.registry)
    args.out.mkdir(parents=True, exist_ok=True)

    trials: list[Trial] = []
    with GymServer() as gym:
        driver = build_driver(headless=not args.no_headless)
        try:
            resolver = LocatorResolver(registry, lambda: driver, propose_patches=False)
            # Seed 0 is the unmutated control. If it is not 100% DIRECT then the
            # registry has drifted from the fixture and no other figure is valid.
            control = run_seed(resolver, driver, gym.url_for(0), 0, registry)
            trials.extend(control)
            off_tier = [t for t in control if t.tier != DIRECT]
            if off_tier:
                print("CONTROL RUN FAILED - fix the registry before trusting any "
                      f"resilience number. Offending: {[t.locator_key for t in off_tier]}",
                      file=sys.stderr)

            for seed in range(args.seed_start, args.seed_start + args.seeds):
                trials.extend(run_seed(resolver, driver, gym.url_for(seed), seed, registry))
                print(f"  seed {seed:>3} done", file=sys.stderr)
        finally:
            driver.quit()

    summary = summarise(trials)
    (args.out / "trials.json").write_text(
        json.dumps([asdict(t) for t in trials], indent=2), encoding="utf-8")
    (args.out / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (args.out / "RESILIENCE.md").write_text(render_markdown(summary), encoding="utf-8")

    print(render_markdown(summary))
    # Non-zero exit on any false heal. This is the gym's own quality gate.
    return 1 if summary["false_heals"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
