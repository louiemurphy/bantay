"""Tests for the gym's own integrity.

A measurement instrument that is not itself tested produces figures that cannot
be relied on. Two properties matter:

1. Determinism: the same seed must produce the same DOM, or no result in the
   resilience report can be reproduced.
2. Ground-truth isolation: the resolver must never be able to read
   `data-bantay-truth`. If it could, the framework would be grading its own work
   and the false-heal rate would carry no information.
"""

from __future__ import annotations

import pytest

from bantay.ai import build_prompt
from bantay.dom import STABLE_ATTRS
from bantay.gym.mutations import (
    ALL_MUTATIONS,
    BY_NAME,
    TRUTH_ATTR,
    mutate,
    plan_for_seed,
)
from bantay.gym.offline import build_corpus, snapshots_from_html
from bantay.gym.server import FIXTURE_DIR

FIXTURE = (FIXTURE_DIR / "checkout.html").read_text(encoding="utf-8")


class TestDeterminism:
    @pytest.mark.parametrize("seed", [1, 7, 42, 99])
    def test_same_seed_gives_identical_html(self, seed):
        first, plan_a = mutate(FIXTURE, seed)
        second, plan_b = mutate(FIXTURE, seed)
        assert first == second
        assert [m.name for m in plan_a] == [m.name for m in plan_b]

    def test_different_seeds_generally_differ(self):
        variants = {mutate(FIXTURE, seed)[0] for seed in range(1, 15)}
        assert len(variants) > 1

    def test_plan_is_stable_across_processes(self):
        # plan_for_seed must not depend on global RNG state, or the report's
        # "mutations applied" column would be a lie.
        import random

        random.seed(1234)
        first = [m.name for m in plan_for_seed(11)]
        random.random()
        assert [m.name for m in plan_for_seed(11)] == first


class TestGroundTruthIsolation:
    def test_truth_attr_is_not_in_stable_attrs(self):
        """The single line of defence: the DOM extractor never harvests it."""
        assert TRUTH_ATTR not in STABLE_ATTRS
        assert not any(attr.startswith("data-bantay") for attr in STABLE_ATTRS)

    @pytest.mark.parametrize("mutation", ALL_MUTATIONS, ids=lambda m: m.name)
    def test_every_mutation_preserves_truth_markers(self, mutation):
        import random

        before = FIXTURE.count(TRUTH_ATTR)
        after = mutation.apply(FIXTURE, random.Random(0)).count(TRUTH_ATTR)
        assert after >= before - 0, (
            f"{mutation.name} destroyed ground-truth markers; every resilience "
            f"number produced with it would be meaningless"
        )

    def test_snapshots_never_carry_truth_markers(self):
        snapshots, truth = snapshots_from_html(FIXTURE)
        assert any(truth.values()), "fixture should have marked elements"
        for snapshot in snapshots:
            assert TRUTH_ATTR not in snapshot.attrs

    def test_ai_prompt_strips_truth_markers_even_if_present(self):
        """Belt and braces. If a future extractor change leaks the marker, the
        LLM still must not receive it."""
        leaked = [{"tag": "button", "attrs": {TRUTH_ATTR: "place_order", "id": "x"}}]
        prompt = build_prompt("k", "d", leaked)
        assert TRUTH_ATTR not in prompt
        assert "place_order" not in prompt


class TestCorpus:
    def test_corpus_has_all_fixture_targets(self):
        corpus = build_corpus(2)
        assert set(corpus["targets"]) == {
            "email", "postcode", "shipping", "promo",
            "total", "apply_promo", "place_order", "confirmation",
        }

    def test_decoy_mutation_actually_creates_ambiguity(self):
        """If inject_decoys stopped producing near-duplicates, the ambiguity
        guard would look effective purely because it was never tested."""
        from bantay.gym.mutations import BY_NAME
        import random

        mutated = BY_NAME["inject_decoys"].apply(FIXTURE, random.Random(0))
        assert mutated.count("Place order") > FIXTURE.count("Place order")


class TestExplicitOperators:
    """Explicit operator selection, so a test can request the change it asserts on."""

    def test_names_override_the_seeded_plan(self):
        _, plan = mutate(FIXTURE, 101, names=["rename_classes"])
        assert [m.name for m in plan] == ["rename_classes"]
        # Seed 101's own plan is different, which is what this parameter avoids
        # relying on.
        assert [m.name for m in plan_for_seed(101)] != ["rename_classes"]

    def test_order_is_preserved(self):
        _, plan = mutate(FIXTURE, 1, names=["strip_ids", "rename_classes"])
        assert [m.name for m in plan] == ["strip_ids", "rename_classes"]

    def test_explicit_plan_is_deterministic(self):
        first, _ = mutate(FIXTURE, 3, names=["scramble_ids"])
        second, _ = mutate(FIXTURE, 3, names=["scramble_ids"])
        assert first == second

    def test_empty_list_applies_nothing(self):
        html, plan = mutate(FIXTURE, 5, names=[])
        assert plan == []
        assert html == FIXTURE

    def test_unknown_operator_names_the_valid_ones(self):
        with pytest.raises(KeyError) as exc:
            mutate(FIXTURE, 1, names=["renmae_classes"])
        message = str(exc.value)
        assert "renmae_classes" in message
        assert "rename_classes" in message, "the error should list what is available"


class TestFixtureSurvivesMutation:
    def test_page_behaviour_does_not_bind_to_ids(self):
        """The fixture's own JavaScript must survive strip_ids and scramble_ids.

        Binding behaviour to ids meant those operators broke the page rather than
        its locators, so journey tests failed for reasons unrelated to locator
        resolution.
        """
        assert "getElementById(" not in FIXTURE, (
            "fixture behaviour must not depend on ids; strip_ids deletes them"
        )

    @pytest.mark.parametrize("operator", ["strip_ids", "scramble_ids", "rename_classes"])
    def test_behaviour_hooks_survive(self, operator):
        import random

        mutated = BY_NAME[operator].apply(FIXTURE, random.Random(0))
        for name in ("confirmation", "place_order", "apply_promo", "total"):
            assert f'{TRUTH_ATTR}="{name}"' in mutated
