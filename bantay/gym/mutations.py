"""DOM mutation operators.

The approach is borrowed from mutation testing: rather than asking whether the
tests pass, inject faults deliberately and measure how much damage the suite
absorbs and where it breaks.

Each operator simulates a real change that breaks locators:

  cosmetic     a CSS framework was swapped, or ids were regenerated
  structural   layout containers were added, or siblings reordered
  semantic     a tag or a test hook changed during a refactor
  hostile      a near-identical decoy element appeared

Every mutation is deterministic given a seed, so any row in the resilience report
can be reproduced with:

    python -m bantay.gym.run --seeds 1 --seed-start <n>

Ground-truth contract
---------------------
Each mutable element carries `data-bantay-truth="<id>"`. Operators must never
touch, move or remove it. That marker is how the report distinguishes a correct
recovery from a confident wrong one. The resolver is not permitted to read it,
and `tests/unit/test_gym_contracts.py` enforces both halves of that contract.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Callable

TRUTH_ATTR = "data-bantay-truth"

COSMETIC, STRUCTURAL, SEMANTIC, HOSTILE = "cosmetic", "structural", "semantic", "hostile"


@dataclass(frozen=True)
class Mutation:
    name: str
    severity: str
    apply: Callable[[str, random.Random], str]
    description: str


def _random_token(rng: random.Random, length: int = 8) -> str:
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(length))


# --- cosmetic --------------------------------------------------------------

def rename_classes(html: str, rng: random.Random) -> str:
    """Replace every class token with a generated one, Tailwind-migration style."""
    mapping: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        original = match.group(2)
        renamed = " ".join(
            mapping.setdefault(token, f"{_random_token(rng, 6)}-{rng.randint(10, 99)}")
            for token in original.split()
        )
        return f'class{match.group(1)}"{renamed}"'

    return re.sub(r'class(\s*=\s*)"([^"]*)"', replace, html)


def strip_ids(html: str, rng: random.Random) -> str:
    """Remove `id` attributes, leaving `#selectors` dead."""
    return re.sub(r'\sid\s*=\s*"[^"]*"', "", html)


def scramble_ids(html: str, rng: random.Random) -> str:
    """Replace ids with build-hash-looking ones, as bundlers do."""
    return re.sub(
        r'(\sid\s*=\s*")([^"]*)(")',
        lambda m: f"{m.group(1)}{_random_token(rng, 5)}_{rng.randint(1000, 9999)}{m.group(3)}",
        html,
    )


# --- structural ------------------------------------------------------------

def wrap_in_divs(html: str, rng: random.Random) -> str:
    """Wrap each form control in extra layout containers."""
    def replace(match: re.Match) -> str:
        depth = rng.randint(1, 3)
        opening = "".join(f'<div class="{_random_token(rng, 5)}">' for _ in range(depth))
        return f"{opening}{match.group(0)}{'</div>' * depth}"

    return re.sub(r"<(input|button|select|textarea)\b[^>]*/?>", replace, html)


def reorder_siblings(html: str, rng: random.Random) -> str:
    """Shuffle list items, invalidating every nth-child and indexed XPath."""
    def replace(match: re.Match) -> str:
        items = re.findall(r"<li\b.*?</li>", match.group(2), re.DOTALL)
        if len(items) < 2:
            return match.group(0)
        rng.shuffle(items)
        return f"{match.group(1)}{''.join(items)}{match.group(3)}"

    return re.sub(r"(<ul\b[^>]*>)(.*?)(</ul>)", replace, html, flags=re.DOTALL)


# --- semantic --------------------------------------------------------------

def button_to_anchor(html: str, rng: random.Random) -> str:
    """`<button>` becomes `<a role="button">` - a very common refactor."""
    html = re.sub(r"<button\b([^>]*)>", r'<a role="button"\1>', html)
    return html.replace("</button>", "</a>")


def drop_test_hooks(html: str, rng: random.Random) -> str:
    """Delete data-test hooks, but never the ground-truth marker."""
    return re.sub(
        r'\sdata-(?!bantay)(?:test|testid|qa)\s*=\s*"[^"]*"', "", html
    )


# --- hostile ---------------------------------------------------------------

def inject_decoys(html: str, rng: random.Random) -> str:
    """Clone buttons into near-identical disabled decoys placed just before the
    real element, with the truth marker stripped.

    This operator targets over-eager healing. A framework that matches on
    similarity alone will select the decoy and report success. The ambiguity
    margin should refuse instead, and the report should record that refusal
    rather than a false heal.
    """
    def replace(match: re.Match) -> str:
        original = match.group(0)
        decoy = re.sub(r'\s' + TRUTH_ATTR + r'\s*=\s*"[^"]*"', "", original)
        decoy = re.sub(r"<(\w+)", r'<\1 disabled aria-hidden="false"', decoy, count=1)
        return decoy + original

    return re.sub(r"<button\b[^>]*>.*?</button>", replace, html, flags=re.DOTALL)


ALL_MUTATIONS: tuple[Mutation, ...] = (
    Mutation("rename_classes", COSMETIC, rename_classes,
             "every CSS class token replaced (framework migration)"),
    Mutation("scramble_ids", COSMETIC, scramble_ids,
             "ids replaced with build hashes (bundler change)"),
    Mutation("strip_ids", COSMETIC, strip_ids,
             "id attributes removed entirely"),
    Mutation("wrap_in_divs", STRUCTURAL, wrap_in_divs,
             "controls wrapped in 1-3 new layout containers"),
    Mutation("reorder_siblings", STRUCTURAL, reorder_siblings,
             "list items shuffled (breaks nth-child / indexed xpath)"),
    Mutation("button_to_anchor", SEMANTIC, button_to_anchor,
             "<button> refactored to <a role=button>"),
    Mutation("drop_test_hooks", SEMANTIC, drop_test_hooks,
             "data-test hooks deleted"),
    Mutation("inject_decoys", HOSTILE, inject_decoys,
             "near-identical disabled decoy inserted before each button"),
)

BY_NAME = {m.name: m for m in ALL_MUTATIONS}


def plan_for_seed(seed: int, count: int | None = None) -> list[Mutation]:
    """Deterministically choose which mutations a given seed applies."""
    rng = random.Random(seed)
    pool = list(ALL_MUTATIONS)
    rng.shuffle(pool)
    if count is None:
        count = rng.randint(1, 3)
    return pool[: max(1, min(count, len(pool)))]


def mutate(html: str, seed: int, count: int | None = None,
           names: list[str] | None = None) -> tuple[str, list[Mutation]]:
    """Apply the plan for `seed`. Same seed always yields the same DOM.

    `names` overrides the seeded plan with an explicit operator list, in the
    order given. A test that asserts something about one specific operator needs
    to request it directly: `plan_for_seed` draws 1-3 operators from the whole
    pool, so a seed chosen because it once produced a class rename will produce
    something else as soon as `ALL_MUTATIONS` is reordered. The seed remains the
    RNG source either way, so results stay reproducible.
    """
    rng = random.Random(seed * 7919)  # decouple plan RNG from operator RNG
    if names is not None:
        unknown = [n for n in names if n not in BY_NAME]
        if unknown:
            raise KeyError(
                f"unknown mutation operator(s): {', '.join(unknown)}. "
                f"Available: {', '.join(sorted(BY_NAME))}"
            )
        plan = [BY_NAME[n] for n in names]
    else:
        plan = plan_for_seed(seed, count)
    for mutation in plan:
        html = mutation.apply(html, rng)
    assert TRUTH_ATTR in html, (
        f"seed {seed}: mutations destroyed the ground-truth markers - "
        "the measurement would be meaningless"
    )
    return html, plan
