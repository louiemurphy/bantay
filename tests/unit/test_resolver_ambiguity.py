"""A declared strategy that matches more than one element must refuse.

This is the tier 1-2 counterpart to `scoring.AMBIGUITY_MARGIN`. That margin only
guards the scorer, and tiers 1-2 return before the scorer is consulted, so without
a uniqueness check the `inject_decoys` operator never reaches the guard designed
to catch it.

The decoy is inserted before the real element and is disabled rather than hidden,
so both document order and the visibility filter favour it. Taking the first hit
therefore resolves onto the decoy at tier DIRECT, with nothing in the log to
indicate it.
"""

from __future__ import annotations

import pytest

from bantay.registry import Locator, LocatorRegistry
from bantay.resolver import DIRECT, AmbiguousStrategy, ElementUnresolvable, LocatorResolver


class FakeElement:
    def __init__(self, name: str, displayed: bool = True):
        self.name = name
        self._displayed = displayed

    def is_displayed(self) -> bool:
        return self._displayed


class FakeDriver:
    """Maps a raw selector string to the elements it matches, in document order."""

    def __init__(self, matches: dict[str, list[FakeElement]]):
        self.matches = matches

    def find_elements(self, by, selector):
        return list(self.matches.get(selector, []))


class StubProposal:
    usable = False
    reason = "stubbed out"


class StubProposer:
    def propose(self, prompt):
        return StubProposal()


def build(matches: dict[str, list[FakeElement]], strategies: list[str],
          fingerprint=None) -> LocatorResolver:
    registry = LocatorRegistry(
        {"apply_promo": Locator(key="apply_promo", strategies=strategies,
                                fingerprint=fingerprint)}
    )
    driver = FakeDriver(matches)
    return LocatorResolver(registry, lambda: driver, proposer=StubProposer(),
                           propose_patches=False)


class TestFindUniqueness:
    def test_single_match_resolves(self):
        resolver = build({"[data-test='apply-promo']": [FakeElement("real")]},
                         ["css:[data-test='apply-promo']"])
        assert resolver._find("css:[data-test='apply-promo']").name == "real"

    def test_two_visible_matches_raise(self):
        resolver = build(
            {"button[type='button']": [FakeElement("decoy"), FakeElement("real")]},
            ["css:button[type='button']"],
        )
        with pytest.raises(AmbiguousStrategy) as exc:
            resolver._find("css:button[type='button']")
        assert "matched 2 elements" in str(exc.value)

    def test_hidden_duplicate_does_not_create_ambiguity(self):
        """Visibility still narrows first: one visible element is unambiguous."""
        resolver = build(
            {"button[type='button']": [FakeElement("hidden", displayed=False),
                                       FakeElement("real")]},
            ["css:button[type='button']"],
        )
        assert resolver._find("css:button[type='button']").name == "real"

    def test_no_match_returns_none(self):
        resolver = build({}, ["css:button[type='button']"])
        assert resolver._find("css:button[type='button']") is None


class TestDecoyIsRefused:
    """The regression the gym found: seeds 7 and 15, `apply_promo`/`place_order`."""

    def test_decoy_is_not_resolved_at_direct_tier(self):
        # `inject_decoys` clones the button, so the decoy keeps `data-test` and
        # only loses the truth marker. The primary strategy matches both.
        resolver = build(
            {"[data-test='apply-promo']": [FakeElement("decoy"), FakeElement("real")]},
            ["css:[data-test='apply-promo']"],
        )
        with pytest.raises(ElementUnresolvable) as exc:
            resolver.resolve("apply_promo")
        assert "AMBIGUOUS" in str(exc.value)

    def test_every_ambiguous_strategy_is_exhausted_before_failing(self):
        resolver = build(
            {
                "[data-test='apply-promo']": [FakeElement("decoy"), FakeElement("real")],
                "button[type='button']": [FakeElement("decoy"), FakeElement("real")],
                "apply-promo": [FakeElement("decoy"), FakeElement("real")],
            },
            ["css:[data-test='apply-promo']", "css:button[type='button']", "id:apply-promo"],
        )
        with pytest.raises(ElementUnresolvable) as exc:
            resolver.resolve("apply_promo")
        message = str(exc.value)
        assert message.count("AMBIGUOUS") == 3, "each strategy should be reported"

    def test_unambiguous_fallback_still_recovers(self):
        """Refusing ambiguity must not cost recovery when a later strategy is clean."""
        resolver = build(
            {
                "[data-test='apply-promo']": [FakeElement("decoy"), FakeElement("real")],
                "button[type='button']": [FakeElement("real")],
            },
            ["css:[data-test='apply-promo']", "css:button[type='button']"],
        )
        resolution = resolver.resolve("apply_promo")
        assert resolution.tier != DIRECT
        assert resolution.element.name == "real"
