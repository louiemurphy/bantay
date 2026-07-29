"""Deterministic similarity scoring between a stored fingerprint and live DOM
candidates.

This module is what allows recovery from most locator rot without an LLM. It is
pure, synchronous, dependency-free Python and is covered by offline unit tests.

Weighting rationale: the attributes that change during a redesign are usually
not the ones that carry meaning. Class names and generated ids churn often,
while accessible names, roles and visible copy change rarely because they are
what users read. Semantics are therefore weighted heavily and styling hooks
barely count.

The ambiguity margin matters as much as the accept threshold. A wrong heal
produces a passing test that no longer exercises the product, so resolution is
refused when the top two candidates score close together and reported as
AMBIGUOUS instead.
"""

from __future__ import annotations

from dataclasses import dataclass

from .dom import ElementSnapshot, tokens

# Sum of weights is 1.0.
WEIGHTS = {
    "accessible_name": 0.30,
    "attrs": 0.25,
    "structure": 0.20,
    "text": 0.10,
    "classes": 0.10,
    "position": 0.05,
}

# Both constants were chosen by measurement. See docs/TUNING.md and `make tune`
# for the sweep and ablation behind them.
#
# Minimum score for a candidate to be considered a match. 0.45 sits on a plateau
# (0.45, 0.50 and 0.55 all yield 85.0% recovery), which is what a committed
# constant wants: small errors in the scorer do not move the outcome. Lower
# values buy more recovery from weaker evidence.
ACCEPT_THRESHOLD = 0.45
# Required gap between the best and second-best candidate, or the result is
# reported as AMBIGUOUS. With no margin the scorer produced 8 false heals across
# 320 trials, including a decoy that scored exactly 1.00. No threshold can reject
# a perfect score, so comparing the top two candidates is the only effective
# guard. A margin of 0.05 removed all 8 at no cost to recovery rate; 0.10 leaves
# headroom.
AMBIGUITY_MARGIN = 0.10
# Mismatched tags are penalised rather than rejected outright, since
# button -> a[role=button] is a common and legitimate refactor.
TAG_MISMATCH_PENALTY = 0.65

# Attributes that identify an element by intent rather than styling. Agreement
# here is strong evidence, so a match earns a weight bonus.
HIGH_SIGNAL_ATTRS = frozenset({"data-test", "data-testid", "data-qa", "name", "role", "type"})


def jaccard(a: set[str], b: set[str]) -> float:
    """Overlap of two token sets. Two empty sets are treated as no evidence."""
    if not a and not b:
        return 0.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def attr_similarity(expected: dict[str, str], actual: dict[str, str]) -> float:
    """Weighted agreement across stable attributes.

    Only attributes present on the *expected* fingerprint are examined. An
    attribute the page has newly grown is not evidence against a match.
    """
    if not expected:
        return 0.0
    earned = 0.0
    possible = 0.0
    for key, want in expected.items():
        weight = 2.0 if key in HIGH_SIGNAL_ATTRS else 1.0
        possible += weight
        have = actual.get(key)
        if have is None:
            continue
        if have == want:
            earned += weight
        else:
            # Partial credit for token overlap, e.g. href path changes.
            earned += weight * 0.5 * jaccard(tokens(want), tokens(have))
    return earned / possible if possible else 0.0


def structure_similarity(expected: tuple[str, ...], actual: tuple[str, ...]) -> float:
    """Length of the shared ancestor-path suffix, normalised.

    Suffix rather than prefix: a wrapper element inserted high in the tree should
    barely register, while a changed immediate parent should.
    """
    if not expected or not actual:
        return 0.0
    shared = 0
    for want, have in zip(reversed(expected), reversed(actual)):
        if want != have:
            break
        shared += 1
    return shared / max(len(expected), len(actual))


def position_similarity(expected: int, actual: int) -> float:
    """Decay by sibling-index distance. The weakest of the six signals."""
    return 1.0 / (1.0 + abs(expected - actual))


@dataclass(frozen=True)
class ScoredCandidate:
    candidate: ElementSnapshot
    score: float
    breakdown: dict[str, float]

    def explain(self) -> str:
        parts = ", ".join(f"{k}={v:.2f}" for k, v in sorted(self.breakdown.items()))
        return f"<{self.candidate.tag}> score={self.score:.3f} ({parts})"


def score_candidate(expected: ElementSnapshot, candidate: ElementSnapshot) -> ScoredCandidate:
    breakdown = {
        "accessible_name": jaccard(
            tokens(expected.accessible_name), tokens(candidate.accessible_name)
        ),
        "attrs": attr_similarity(expected.attrs, candidate.attrs),
        "structure": structure_similarity(expected.ancestor_path, candidate.ancestor_path),
        "text": jaccard(tokens(expected.text), tokens(candidate.text)),
        "classes": jaccard(set(expected.classes), set(candidate.classes)),
        "position": position_similarity(expected.sibling_index, candidate.sibling_index),
    }
    score = sum(WEIGHTS[key] * value for key, value in breakdown.items())
    if expected.tag != candidate.tag:
        score *= TAG_MISMATCH_PENALTY
        breakdown["tag_penalty"] = TAG_MISMATCH_PENALTY
    return ScoredCandidate(candidate=candidate, score=round(score, 4), breakdown=breakdown)


@dataclass(frozen=True)
class MatchDecision:
    """Outcome of scoring a whole candidate set."""

    verdict: str  # MATCH | AMBIGUOUS | NO_MATCH
    best: ScoredCandidate | None
    runner_up: ScoredCandidate | None
    reason: str

    @property
    def matched(self) -> bool:
        return self.verdict == "MATCH"


def best_match(
    expected: ElementSnapshot,
    candidates: list[ElementSnapshot],
    threshold: float = ACCEPT_THRESHOLD,
    margin: float = AMBIGUITY_MARGIN,
) -> MatchDecision:
    """Pick the single best candidate, or refuse to pick one."""
    if not candidates:
        return MatchDecision("NO_MATCH", None, None, "no candidates harvested from DOM")

    ranked = sorted(
        (score_candidate(expected, c) for c in candidates),
        key=lambda s: s.score,
        reverse=True,
    )
    best, runner_up = ranked[0], (ranked[1] if len(ranked) > 1 else None)

    if best.score < threshold:
        return MatchDecision(
            "NO_MATCH", best, runner_up,
            f"best score {best.score:.3f} below threshold {threshold:.2f}",
        )

    if runner_up is not None and (best.score - runner_up.score) < margin:
        return MatchDecision(
            "AMBIGUOUS", best, runner_up,
            f"top two candidates within {best.score - runner_up.score:.3f} "
            f"(margin {margin:.2f}) - refusing to guess",
        )

    return MatchDecision("MATCH", best, runner_up, f"matched at {best.score:.3f}")
