"""Scoring algorithm tests.

These run in milliseconds with no browser and no network. The decision logic of a
test framework warrants its own unit tests, and logic only reachable through
Selenium tends not to get them.
"""

from __future__ import annotations

import pytest

from bantay.dom import ElementSnapshot
from bantay.scoring import (
    ACCEPT_THRESHOLD,
    AMBIGUITY_MARGIN,
    attr_similarity,
    best_match,
    jaccard,
    score_candidate,
    structure_similarity,
)


def element(**overrides) -> ElementSnapshot:
    base = dict(
        tag="button",
        text="place order",
        accessible_name="place order",
        attrs={"id": "place-order", "type": "submit", "data-test": "place-order"},
        classes=("btn", "btn-primary"),
        ancestor_path=("html", "body", "form", "div"),
        sibling_index=1,
        xpath="/html[1]/body[1]/form[1]/div[1]/button[2]",
    )
    base.update(overrides)
    return ElementSnapshot(**base)


class TestPrimitives:
    def test_jaccard_treats_two_empty_sets_as_no_evidence(self):
        # Not 1.0: two elements both lacking text is not evidence they match.
        assert jaccard(set(), set()) == 0.0

    def test_jaccard_is_symmetric(self):
        a, b = {"place", "order"}, {"order", "now"}
        assert jaccard(a, b) == jaccard(b, a)

    def test_attr_similarity_ignores_newly_added_attributes(self):
        # A page growing an attribute is not evidence against a match.
        expected = {"name": "email"}
        assert attr_similarity(expected, {"name": "email", "aria-busy": "true"}) == 1.0

    def test_attr_similarity_weights_high_signal_attributes_higher(self):
        # Agreement on data-test should beat agreement on title.
        high = attr_similarity({"data-test": "x", "title": "y"}, {"data-test": "x"})
        low = attr_similarity({"data-test": "x", "title": "y"}, {"title": "y"})
        assert high > low

    def test_structure_compares_suffix_not_prefix(self):
        expected = ("html", "body", "form", "div")
        # New wrapper high in the tree: should still retain most similarity.
        near_root = structure_similarity(expected, ("html", "body", "main", "form", "div"))
        # Changed immediate parent: should be punished harder.
        near_leaf = structure_similarity(expected, ("html", "body", "form", "section"))
        assert near_root > near_leaf


class TestScoring:
    def test_identical_elements_score_one(self):
        assert score_candidate(element(), element()).score == pytest.approx(1.0)

    def test_class_rename_alone_is_survivable(self):
        mutated = element(classes=("xk3ld-42", "qq81ms-17"))
        assert score_candidate(element(), mutated).score >= ACCEPT_THRESHOLD

    def test_tag_change_is_penalised_but_not_fatal(self):
        # button -> a[role=button] is a legitimate refactor, not a new element.
        anchor = element(tag="a", attrs={**element().attrs, "role": "button"})
        scored = score_candidate(element(), anchor)
        assert scored.score < 1.0
        assert "tag_penalty" in scored.breakdown

    def test_unrelated_element_scores_below_threshold(self):
        unrelated = ElementSnapshot(
            tag="input", text="", accessible_name="email address",
            attrs={"name": "email", "type": "email"}, classes=("input",),
            ancestor_path=("html", "body", "form", "div"), sibling_index=1,
        )
        assert score_candidate(element(), unrelated).score < ACCEPT_THRESHOLD


class TestBestMatch:
    def test_empty_candidate_list_is_no_match_not_a_crash(self):
        decision = best_match(element(), [])
        assert decision.verdict == "NO_MATCH"
        assert not decision.matched
        assert "no candidates" in decision.reason

    def test_clear_winner_is_matched(self):
        decoyish = ElementSnapshot(tag="input", accessible_name="email address",
                                  attrs={"name": "email"})
        decision = best_match(element(), [element(classes=("new-btn",)), decoyish])
        assert decision.matched

    def test_near_identical_decoy_is_refused_not_guessed(self):
        """Regression test for the ambiguity guard.

        A decoy that scores identically to the target cannot be rejected by any
        confidence threshold, because a perfect score passes every threshold. The
        only defence is comparing the top two candidates. Measured on the gym
        corpus, removing this guard produced 8 false heals in 320 trials, one of
        them scoring exactly 1.00.
        """
        target = element()
        decoy = element(xpath="/html[1]/body[1]/form[1]/div[1]/button[1]")
        decision = best_match(target, [target, decoy])
        assert decision.verdict == "AMBIGUOUS"
        assert not decision.matched
        assert "refusing to guess" in decision.reason

    def test_margin_of_zero_would_have_guessed(self):
        """Documents what the guard prevents."""
        target = element()
        decoy = element(xpath="/other")
        assert best_match(target, [target, decoy], margin=0.0).matched

    def test_below_threshold_reports_the_closest_candidate(self):
        # A useful failure names the near miss instead of just saying "not found".
        far = ElementSnapshot(tag="div", text="unrelated", accessible_name="unrelated")
        decision = best_match(element(), [far])
        assert decision.verdict == "NO_MATCH"
        assert decision.best is not None
        assert "score" in decision.best.explain()

    def test_defaults_are_the_measured_values(self):
        # Guards against the constants being changed without re-running the sweep
        # documented in docs/TUNING.md.
        assert (ACCEPT_THRESHOLD, AMBIGUITY_MARGIN) == (0.45, 0.10)
