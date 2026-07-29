"""The locator registry.

Locators live in YAML rather than in test code, for three reasons:

1. A locator change is a data change and should not require editing a test.
2. One element can carry several strategies, tried in declared order.
3. The stored fingerprint gives the resolver something to aim at once every
   declared strategy has gone stale.

The registry is never rewritten at runtime. When the resolver heals an element it
writes a patch proposal to `reports/patches/` instead, so a change to test data
is always a reviewable event rather than a silent one.
"""

from __future__ import annotations

import difflib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

import yaml

from .dom import ElementSnapshot


@dataclass
class Locator:
    """One named element: an ordered strategy list plus a fingerprint."""

    key: str
    strategies: list[str] = field(default_factory=list)
    fingerprint: ElementSnapshot | None = None
    description: str = ""

    @classmethod
    def from_dict(cls, key: str, raw: dict[str, Any]) -> "Locator":
        strategies = raw.get("strategies") or ([raw["strategy"]] if raw.get("strategy") else [])
        fp = raw.get("fingerprint")
        return cls(
            key=key,
            strategies=list(strategies),
            fingerprint=ElementSnapshot.from_dict(fp) if fp else None,
            description=raw.get("description", ""),
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"strategies": list(self.strategies)}
        if self.description:
            out["description"] = self.description
        if self.fingerprint:
            out["fingerprint"] = self.fingerprint.to_dict()
        return out


class LocatorNotFound(KeyError):
    """Raised for an unknown registry key - a typo, not a page change."""


class LocatorRegistry:
    """All locators for one page or component, loaded from a YAML file."""

    def __init__(self, locators: dict[str, Locator], source: Path | None = None):
        self._locators = locators
        self.source = source

    @classmethod
    def load(cls, path: str | Path) -> "LocatorRegistry":
        path = Path(path)
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entries = raw.get("locators", raw)
        locators = {key: Locator.from_dict(key, value) for key, value in entries.items()}
        return cls(locators, source=path)

    @classmethod
    def load_dir(cls, directory: str | Path) -> "LocatorRegistry":
        """Merge every `*.yaml` in a directory, namespaced by filename stem.

        `checkout.yaml` holding `submit` becomes the key `checkout.submit`.
        """
        merged: dict[str, Locator] = {}
        directory = Path(directory)
        for path in sorted(directory.glob("*.yaml")):
            page = cls.load(path)
            for key, locator in page._locators.items():
                namespaced = f"{path.stem}.{key}"
                merged[namespaced] = Locator(
                    key=namespaced,
                    strategies=locator.strategies,
                    fingerprint=locator.fingerprint,
                    description=locator.description,
                )
        return cls(merged, source=directory)

    def __contains__(self, key: object) -> bool:
        return key in self._locators

    def __iter__(self) -> Iterator[Locator]:
        return iter(self._locators.values())

    def __len__(self) -> int:
        return len(self._locators)

    def get(self, key: str) -> Locator:
        try:
            return self._locators[key]
        except KeyError as exc:
            raise LocatorNotFound(
                f"No locator registered as '{key}'.{self._suggest(key)}"
            ) from exc

    def _suggest(self, key: str) -> str:
        """Best-effort "did you mean" for an unknown key.

        Substring matching runs first, since it catches a correctly spelled key
        under the wrong namespace. Edit distance is the fallback, because
        substring matching cannot catch a transposition: 'emial' shares no
        substring with 'email' but is one swap away from it.
        """
        leaf = key.split(".")[-1]
        registered = list(self._locators)
        close = [k for k in registered if leaf in k]
        if not close:
            leaves = [k.split(".")[-1] for k in registered]
            near = difflib.get_close_matches(leaf, leaves, n=3, cutoff=0.6)
            close = [k for k in registered if k.split(".")[-1] in near]
        return f" Did you mean: {', '.join(sorted(close)[:3])}?" if close else ""

    def keys(self) -> list[str]:
        return sorted(self._locators)


def write_patch_proposal(
    locator: Locator,
    healed_strategy: str,
    observed: ElementSnapshot,
    evidence: str,
    out_dir: str | Path = "reports/patches",
) -> Path:
    """Emit a reviewable patch instead of editing the registry in place.

    The output is a small YAML fragment plus the evidence that produced it, so a
    reviewer can decide whether the locator drifted or the product itself changed.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%S")
    path = out_dir / f"{locator.key.replace('.', '_')}-{stamp}.patch.yaml"

    proposed = dict.fromkeys([healed_strategy] + locator.strategies)  # dedupe, keep order
    payload = {
        "locator_key": locator.key,
        "evidence": evidence,
        "current": {"strategies": locator.strategies},
        "proposed": {
            "strategies": list(proposed),
            "fingerprint": observed.to_dict(),
        },
        "review_checklist": [
            "Did the element move, or did the product's behaviour change?",
            "Is the new strategy semantic (role/label/test-id) or incidental (nth-child)?",
            "Should the application add a stable test hook instead?",
        ],
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    (out_dir / f"{path.stem}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path
