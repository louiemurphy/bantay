"""The resolution pipeline.

Four tiers, in strictly increasing order of cost and decreasing order of trust:

  1. DIRECT    - a declared strategy worked. The overwhelmingly common case.
  2. FALLBACK  - a later declared strategy worked. Registry is drifting.
  3. SCORED    - no declared strategy worked; the deterministic scorer found an
                 unambiguous match against the stored fingerprint.
  4. ASSISTED  - the scorer found nothing; an LLM proposed a selector, and that
                 proposal then had to survive the same deterministic scoring.

Tiers 2 to 4 are recoveries, not successes. They resolve the element so a run can
continue and produce a complete report instead of stopping at the first stale
locator, but each one is tagged, counted and reported. A rising recovery rate is
the signal that a suite is drifting away from the product, which is the reason
every outcome is graded rather than reduced to found/not-found.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from .ai import LocatorProposer, build_proposer, build_prompt
from .dom import EXTRACT_CANDIDATES_JS, ElementSnapshot
from .registry import Locator, LocatorRegistry, write_patch_proposal
from .scoring import MatchDecision, best_match

log = logging.getLogger("bantay.resolver")

DIRECT, FALLBACK, SCORED, ASSISTED, FAILED = "DIRECT", "FALLBACK", "SCORED", "ASSISTED", "FAILED"
RECOVERY_TIERS = (FALLBACK, SCORED, ASSISTED)


class ElementUnresolvable(AssertionError):
    """Every tier failed. Deliberately an AssertionError: an element that cannot
    be found is a test failure, not an infrastructure error to be retried."""


class AmbiguousStrategy(Exception):
    """A declared strategy matched more than one element.

    Resolving on the first hit would let document order decide which element a
    test acts on, and a near-identical decoy inserted before the real element
    wins that ordering. Raised rather than swallowed so the strategy walk
    continues, and so that when every declared strategy is ambiguous the decision
    falls through to the scorer's ambiguity margin.
    """


@dataclass
class Resolution:
    """What happened, and why - the unit of telemetry for the whole framework."""

    key: str
    tier: str
    strategy: str | None
    element: Any = None
    attempts: list[str] = field(default_factory=list)
    decision: MatchDecision | None = None
    notes: str = ""

    @property
    def recovered(self) -> bool:
        return self.tier in RECOVERY_TIERS

    def summary(self) -> str:
        line = f"[{self.tier}] {self.key} -> {self.strategy or 'unresolved'}"
        return f"{line} :: {self.notes}" if self.notes else line


class LocatorResolver:
    """Browser-facing resolution. Selenium is injected, not imported, so the
    pipeline can be exercised with a fake driver in unit tests."""

    def __init__(
        self,
        registry: LocatorRegistry,
        driver_factory: Callable[[], Any],
        proposer: LocatorProposer | None = None,
        propose_patches: bool = True,
    ):
        self.registry = registry
        self._driver_factory = driver_factory
        self.proposer = proposer or build_proposer()
        self.propose_patches = propose_patches
        self.history: list[Resolution] = []

    @property
    def driver(self) -> Any:
        return self._driver_factory()

    # -- tier 1 & 2 ------------------------------------------------------

    def _try_strategies(self, locator: Locator) -> tuple[Any, str | None, int, list[str]]:
        """Walk declared strategies in order. Returns first hit and its index."""
        attempts: list[str] = []
        for index, strategy in enumerate(locator.strategies):
            try:
                element = self._find(strategy)
            except AmbiguousStrategy as exc:
                attempts.append(f"{strategy} -> AMBIGUOUS ({exc})")
                continue
            except Exception as exc:
                attempts.append(f"{strategy} -> {type(exc).__name__}")
                continue
            if element is not None:
                attempts.append(f"{strategy} -> HIT")
                return element, strategy, index, attempts
            attempts.append(f"{strategy} -> no match")
        return None, None, -1, attempts

    def _find(self, strategy: str) -> Any:
        """Resolve one strategy string. Supports `css:`, `xpath:`, `id:`,
        `name:`, `text:` prefixes; bare strings are treated as CSS.

        Returns None when nothing matches, and raises `AmbiguousStrategy` when
        more than one candidate matches. Visible elements are preferred, but a
        strategy that still cannot single one out is refused rather than
        resolved on its first hit."""
        from selenium.webdriver.common.by import By

        prefix, _, value = strategy.partition(":")
        prefix = prefix.strip().lower()
        if not value:
            prefix, value = "css", strategy
        mapping = {
            "css": (By.CSS_SELECTOR, value),
            "xpath": (By.XPATH, value),
            "id": (By.ID, value),
            "name": (By.NAME, value),
            "text": (By.XPATH, f"//*[normalize-space(text())={_xpath_literal(value)}]"),
        }
        if prefix not in mapping:
            mapping[prefix] = (By.CSS_SELECTOR, strategy)
        by, selector = mapping[prefix]
        found = self.driver.find_elements(by, selector)
        if not found:
            return None
        candidates = [e for e in found if _is_displayed(e)] or found
        if len(candidates) > 1:
            raise AmbiguousStrategy(f"matched {len(candidates)} elements")
        return candidates[0]

    # -- tier 3 ----------------------------------------------------------

    def harvest_candidates(self) -> list[ElementSnapshot]:
        raw = self.driver.execute_script(EXTRACT_CANDIDATES_JS) or []
        return [ElementSnapshot.from_dict(item) for item in raw]

    # -- orchestration ---------------------------------------------------

    def resolve(self, key: str) -> Resolution:
        locator = self.registry.get(key)

        element, strategy, index, attempts = self._try_strategies(locator)
        if element is not None:
            tier = DIRECT if index == 0 else FALLBACK
            note = "" if tier == DIRECT else (
                f"primary strategy '{locator.strategies[0]}' is stale; "
                f"fell back to position {index + 1}"
            )
            return self._record(Resolution(key, tier, strategy, element, attempts, notes=note))

        if locator.fingerprint is None:
            raise ElementUnresolvable(
                f"'{key}': all {len(locator.strategies)} strategies failed and no "
                f"fingerprint is recorded, so recovery is not possible.\n"
                f"  attempts: {attempts}\n"
                f"  fix: add a `fingerprint:` block to this entry in the registry."
            )

        candidates = self.harvest_candidates()
        decision = best_match(locator.fingerprint, candidates)

        if decision.matched and decision.best is not None:
            healed = f"xpath:{decision.best.candidate.xpath}"
            try:
                element = self._find(healed)
            except AmbiguousStrategy:
                # A healed selector that cannot single out its own target is no
                # better than the stale one it would replace.
                element = None
            if element is not None:
                self._maybe_patch(locator, healed, decision, tier=SCORED)
                return self._record(Resolution(
                    key, SCORED, healed, element, attempts, decision,
                    notes=f"deterministic recovery: {decision.best.explain()}",
                ))

        assisted = self._ask_model(locator, candidates, decision)
        if assisted is not None:
            element, healed, note = assisted
            self._maybe_patch(locator, healed, decision, tier=ASSISTED)
            return self._record(
                Resolution(key, ASSISTED, healed, element, attempts, decision, notes=note)
            )

        self._record(Resolution(key, FAILED, None, None, attempts, decision))
        raise ElementUnresolvable(
            f"'{key}' could not be resolved by any tier.\n"
            f"  declared attempts: {attempts}\n"
            f"  candidates harvested: {len(candidates)}\n"
            f"  scorer verdict: {decision.verdict} - {decision.reason}\n"
            + (f"  closest was {decision.best.explain()}\n" if decision.best else "")
            + "  This is most likely a real product change. Review before healing."
        )

    def _ask_model(
        self, locator: Locator, candidates: list[ElementSnapshot], decision: MatchDecision
    ) -> tuple[Any, str, str] | None:
        """Last resort. The proposal is verified by the scorer before use."""
        prompt = build_prompt(
            locator.key,
            locator.description or f"element previously matched by {locator.strategies}",
            [c.to_dict() for c in candidates],
        )
        proposal = self.proposer.propose(prompt)
        if not proposal.usable:
            log.debug("AI declined for %s: %s", locator.key, proposal.reason)
            return None

        strategy = f"css:{proposal.selector}"
        try:
            element = self._find(strategy)
        except Exception as exc:
            log.debug("AI selector invalid for %s: %s", locator.key, exc)
            return None
        if element is None:
            return None

        # The model does not get the final word. Verify what it pointed at.
        observed = self._snapshot_of(element)
        verify = best_match(locator.fingerprint, [observed]) if locator.fingerprint else None
        if verify is not None and not verify.matched:
            log.debug(
                "AI proposal for %s rejected by scorer: %s", locator.key, verify.reason
            )
            return None

        note = (
            f"AI-assisted ({proposal.source}, confidence {proposal.confidence:.2f}): "
            f"{proposal.reason} | scorer confirmed"
        )
        return element, strategy, note

    def _snapshot_of(self, element: Any) -> ElementSnapshot:
        """Fingerprint a single live element, reusing the same JS extractor."""
        raw = self.driver.execute_script(
            "const el = arguments[0]; " + EXTRACT_CANDIDATES_JS.replace(
                "return Array.from(document.querySelectorAll(SELECTOR))",
                "return Array.from([el])",
            ),
            element,
        ) or []
        return ElementSnapshot.from_dict(raw[0]) if raw else ElementSnapshot(tag="unknown")

    def _maybe_patch(
        self, locator: Locator, strategy: str, decision: MatchDecision, tier: str
    ) -> None:
        if not self.propose_patches or decision.best is None:
            return
        write_patch_proposal(
            locator=locator,
            healed_strategy=strategy,
            observed=decision.best.candidate,
            evidence=f"tier={tier} {decision.verdict}: {decision.reason}",
        )

    def _record(self, resolution: Resolution) -> Resolution:
        self.history.append(resolution)
        if resolution.recovered:
            log.warning("%s", resolution.summary())
        return resolution

    def stats(self) -> dict[str, int]:
        counts = {tier: 0 for tier in (DIRECT, FALLBACK, SCORED, ASSISTED, FAILED)}
        for item in self.history:
            counts[item.tier] = counts.get(item.tier, 0) + 1
        return counts


def _xpath_literal(value: str) -> str:
    """Quote a string for XPath 1.0, which has no escape syntax."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(f"'{p}'" for p in parts) + ")"


def _is_displayed(element: Any) -> bool:
    try:
        return bool(element.is_displayed())
    except Exception:
        return True
