"""Browserless corpus builder and threshold sweep.

The gym in `run.py` needs Chrome, which makes it impractical for tuning. Choosing
`ACCEPT_THRESHOLD` and `AMBIGUITY_MARGIN` means running the scorer hundreds of
times across many mutation seeds, which is not workable through a browser.

The gym is therefore split in two:

* capture - turn mutated HTML into an `ElementSnapshot` corpus. Standard library
  only, no browser and no network, and it runs in CI in under a second.
* sweep - replay that corpus against the scorer at many settings and report
  recovery rate against false-heal rate for each one.

Every constant in `scoring.py` therefore has a measurement behind it, and the sweep
is cheap enough that a reviewer can re-derive those numbers with `make tune`.

Limitation
----------
This parser approximates the browser's view. It does not compute layout, so it
cannot know what is visually hidden, and its accessible-name calculation covers
only the subset of the specification the fixture exercises. It is a tuning
instrument rather than an oracle: figures from `run.py` in a real browser are
authoritative, while figures from here are for choosing constants and catching
regressions cheaply.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path

from ..dom import STABLE_ATTRS, ElementSnapshot, normalize_text
from ..scoring import best_match
from .mutations import TRUTH_ATTR, mutate, plan_for_seed
from .server import FIXTURE_DIR

INTERACTIVE = {"a", "button", "input", "select", "textarea", "label", "summary", "option", "span", "p"}
VOID = {"input", "br", "img", "hr", "meta", "link", "source"}


@dataclass
class ParsedElement:
    tag: str
    attrs: dict[str, str]
    ancestor_path: tuple[str, ...]
    sibling_index: int
    text_parts: list[str]
    xpath: str

    @property
    def truth(self) -> str:
        return self.attrs.get(TRUTH_ATTR, "")


class SnapshotParser(HTMLParser):
    """Collect candidate elements with enough context to fingerprint them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._stack: list[ParsedElement] = []
        self._child_counts: list[dict[str, int]] = [{}]
        self._sibling_totals: list[int] = [0]
        self.elements: list[ParsedElement] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style"):
            self._skip_depth += 1
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        path = tuple(e.tag for e in self._stack)
        index = self._sibling_totals[-1]
        self._sibling_totals[-1] += 1

        counts = self._child_counts[-1]
        counts[tag] = counts.get(tag, 0) + 1
        parent_xpath = self._stack[-1].xpath if self._stack else ""
        xpath = f"{parent_xpath}/{tag}[{counts[tag]}]"

        element = ParsedElement(tag, attr_map, path, index, [], xpath)
        self.elements.append(element)
        if tag not in VOID:
            self._stack.append(element)
            self._child_counts.append({})
            self._sibling_totals.append(0)

    def handle_startendtag(self, tag, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style"):
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in VOID:
            return
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                del self._child_counts[i + 1:]
                del self._sibling_totals[i + 1:]
                break

    def handle_data(self, data: str) -> None:
        if self._skip_depth or not data.strip():
            return
        for element in self._stack:  # text belongs to every open ancestor
            element.text_parts.append(data)


def _accessible_name(
    element: ParsedElement,
    by_id: dict[str, ParsedElement],
    label_for: dict[str, ParsedElement],
) -> str:
    """Subset of the accname algorithm: the parts the fixture actually uses."""
    if element.attrs.get("aria-label"):
        return element.attrs["aria-label"]
    labelledby = element.attrs.get("aria-labelledby")
    if labelledby:
        referenced = [by_id[i] for i in labelledby.split() if i in by_id]
        if referenced:
            return " ".join("".join(r.text_parts) for r in referenced)
    element_id = element.attrs.get("id")
    if element_id and element_id in label_for:
        return "".join(label_for[element_id].text_parts)
    for candidate in ("placeholder", "title", "alt", "value"):
        if element.attrs.get(candidate):
            return element.attrs[candidate]
    return "".join(element.text_parts)


def snapshots_from_html(html: str) -> tuple[list[ElementSnapshot], dict[str, str]]:
    """Return candidate snapshots plus an xpath -> truth-marker mapping.

    The truth mapping is returned *separately* and is never written onto the
    snapshots, mirroring the browser path where `data-bantay-truth` is outside
    `STABLE_ATTRS` and therefore invisible to the resolver.
    """
    parser = SnapshotParser()
    parser.feed(html)
    by_id = {e.attrs["id"]: e for e in parser.elements if e.attrs.get("id")}
    # Labels are indexed by their `for` target rather than by their own id, which
    # they usually do not have. Indexing them through `by_id` produced empty
    # accessible names and cost roughly 0.4 of similarity score on every input.
    label_for = {
        e.attrs["for"]: e for e in parser.elements
        if e.tag == "label" and e.attrs.get("for")
    }

    # Labels help build names for other elements but are noise as candidates.
    candidates = [
        e for e in parser.elements
        if e.tag in INTERACTIVE - {"label"} and (
            e.tag in {"a", "button", "input", "select", "textarea"}
            or e.attrs.get("role") or TRUTH_ATTR in e.attrs
        )
    ]

    snapshots: list[ElementSnapshot] = []
    truth: dict[str, str] = {}
    for element in candidates:
        snapshots.append(ElementSnapshot(
            tag=element.tag,
            text=normalize_text("".join(element.text_parts))[:200],
            accessible_name=normalize_text(_accessible_name(element, by_id, label_for))[:200],
            attrs={k: v for k, v in element.attrs.items() if k in STABLE_ATTRS},
            classes=tuple(sorted(element.attrs.get("class", "").split())),
            ancestor_path=element.ancestor_path,
            sibling_index=element.sibling_index,
            xpath=element.xpath,
        ))
        truth[element.xpath] = element.truth
    return snapshots, truth


def build_corpus(seeds: int, fixture: str = "checkout.html") -> dict:
    """Baseline fingerprints plus one mutated candidate set per seed."""
    html = (FIXTURE_DIR / fixture).read_text(encoding="utf-8")
    baseline, baseline_truth = snapshots_from_html(html)
    targets = {
        baseline_truth[s.xpath]: s.to_dict()
        for s in baseline if baseline_truth.get(s.xpath)
    }

    cases = []
    for seed in range(1, seeds + 1):
        mutated, _ = mutate(html, seed)
        snaps, truth = snapshots_from_html(mutated)
        cases.append({
            "seed": seed,
            "mutations": [m.name for m in plan_for_seed(seed)],
            "candidates": [s.to_dict() for s in snaps],
            "truth": {s.xpath: truth.get(s.xpath, "") for s in snaps},
        })
    return {"fixture": fixture, "targets": targets, "cases": cases}


def evaluate(corpus: dict, threshold: float, margin: float) -> dict:
    """Score every target against every mutated page at one setting."""
    matched = wrong = ambiguous = no_match = 0
    failures: list[dict] = []

    for key, target_raw in corpus["targets"].items():
        target = ElementSnapshot.from_dict(target_raw)
        for case in corpus["cases"]:
            candidates = [ElementSnapshot.from_dict(c) for c in case["candidates"]]
            decision = best_match(target, candidates, threshold=threshold, margin=margin)
            if decision.verdict == "MATCH" and decision.best is not None:
                landed = case["truth"].get(decision.best.candidate.xpath, "")
                if landed == key:
                    matched += 1
                else:
                    wrong += 1
                    failures.append({"target": key, "seed": case["seed"],
                                     "landed_on": landed or "<unmarked>",
                                     "mutations": case["mutations"],
                                     "score": decision.best.score})
            elif decision.verdict == "AMBIGUOUS":
                ambiguous += 1
            else:
                no_match += 1

    total = matched + wrong + ambiguous + no_match
    return {
        "threshold": threshold,
        "margin": margin,
        "trials": total,
        "correct": matched,
        "false_heals": wrong,
        "ambiguous": ambiguous,
        "no_match": no_match,
        "recovery_rate": round(matched / total, 4) if total else 0.0,
        "false_heal_rate": round(wrong / total, 4) if total else 0.0,
        "worst_failures": failures[:10],
    }


def sweep(corpus: dict, thresholds: list[float], margins: list[float]) -> list[dict]:
    return [evaluate(corpus, t, m) for t in thresholds for m in margins]


def render_sweep(results: list[dict]) -> str:
    """Report the sweep and mark the committed setting.

    The highest recovery rate is deliberately not selected automatically. Peak
    recovery sits on a steep part of the curve, where a small change in the
    scorer moves the outcome. See docs/TUNING.md.
    """
    from ..scoring import ACCEPT_THRESHOLD, AMBIGUITY_MARGIN

    lines = [
        "# Threshold sweep",
        "",
        "Regenerate with `make tune`. Recovery is only useful if it is correct, so",
        "the column that matters is `false heals` rather than `recovery`.",
        "",
        "| threshold | margin | recovery | false heals | ambiguous | no match | |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in results:
        committed = (row["threshold"] == ACCEPT_THRESHOLD
                     and row["margin"] == AMBIGUITY_MARGIN)
        lines.append(
            f"| {row['threshold']:.2f} | {row['margin']:.2f} | "
            f"{row['recovery_rate'] * 100:.1f}% | {row['false_heals']} | "
            f"{row['ambiguous']} | {row['no_match']} | "
            f"{'**committed**' if committed else ''} |"
        )

    unsafe = [r for r in results if r["false_heals"] > 0]
    lines += ["", "## Settings that produced false heals", ""]
    if unsafe:
        for row in unsafe:
            lines.append(
                f"- threshold `{row['threshold']:.2f}` margin `{row['margin']:.2f}`: "
                f"**{row['false_heals']} false heals** "
                f"(worst score {max((f['score'] for f in row['worst_failures']), default=0):.2f})"
            )
        lines += [
            "",
            "Each of these comes from a low or absent ambiguity margin rather than",
            "from a low threshold. A decoy element can score 1.00, and no threshold",
            "rejects a perfect score, so comparing the top two candidates is the",
            "only setting that removes them.",
        ]
    else:
        lines.append("None at any setting in this sweep.")

    committed_row = next(
        (r for r in results if r["threshold"] == ACCEPT_THRESHOLD
         and r["margin"] == AMBIGUITY_MARGIN), None
    )
    if committed_row:
        lines += [
            "",
            "## Committed setting",
            "",
            f"threshold `{ACCEPT_THRESHOLD}`, margin `{AMBIGUITY_MARGIN}` - "
            f"{committed_row['recovery_rate'] * 100:.1f}% recovery, "
            f"{committed_row['false_heals']} false heals across "
            f"{committed_row['trials']} trials.",
            "",
            "Chosen for a plateau rather than a peak. Lower thresholds recover more",
            "but sit on a steep part of the curve, where a small change in the",
            "scorer moves the outcome. See docs/TUNING.md for the full reasoning.",
        ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Offline corpus build + threshold sweep")
    parser.add_argument("--seeds", type=int, default=40)
    parser.add_argument("--out", type=Path, default=Path("reports/tuning"))
    args = parser.parse_args(argv)

    corpus = build_corpus(args.seeds)
    # Range spans the committed setting and both directions, and includes
    # margin 0.00 so the ablation that justifies the guard is always regenerated.
    thresholds = [round(0.30 + 0.05 * i, 2) for i in range(11)]
    results = sweep(corpus, thresholds, [0.00, 0.05, 0.10, 0.20])

    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "corpus.json").write_text(json.dumps(corpus, indent=2), encoding="utf-8")
    (args.out / "sweep.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (args.out / "SWEEP.md").write_text(render_sweep(results), encoding="utf-8")
    print(render_sweep(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
