"""LLM interface for locator proposals.

Every model call goes through a `LocatorProposer`. Three backends are available,
selected by the `BANTAY_AI` environment variable:

* `DisabledProposer` - the default. Returns nothing, so the suite is fully
  deterministic and needs no credentials.
* `CassetteProposer` - replays recorded responses from `cassettes/`, keyed by a
  content hash of the prompt, so an AI-assisted result can be reproduced in CI
  without an API key.
* `AnthropicProposer` - live calls, opt-in via `BANTAY_AI=live`.

The proposer's only output is a candidate selector. It cannot report a test
result, cannot edit the registry, and never sees the ground-truth markers the
mutation gym uses for measurement. Any selector it returns is checked against the
live DOM and re-scored by the same deterministic scorer as everything else, and
discarded if it does not pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CASSETTE_DIR = Path(os.environ.get("BANTAY_CASSETTES", "cassettes"))

SYSTEM_PROMPT = """You repair broken web element locators.

You receive: a description of the element that a test is looking for, and a \
pruned list of elements currently present on the page.

Reply with JSON only, no prose and no code fences:
{"selector": "<css selector>", "confidence": <0.0-1.0>, "reason": "<one short sentence>"}

Rules:
- Prefer selectors built from semantics: [data-test], [role], [name], [type], \
visible text. Avoid nth-child and generated class names.
- If no element plausibly matches, reply {"selector": null, "confidence": 0.0, \
"reason": "..."}. Guessing is worse than abstaining.
- Never invent an element that is not in the supplied list."""


@dataclass(frozen=True)
class Proposal:
    selector: str | None
    confidence: float
    reason: str
    source: str  # disabled | cassette | live

    @property
    def usable(self) -> bool:
        return bool(self.selector) and self.confidence >= 0.5

    @classmethod
    def empty(cls, source: str, reason: str) -> "Proposal":
        return cls(selector=None, confidence=0.0, reason=reason, source=source)


class LocatorProposer(Protocol):
    def propose(self, prompt: str) -> Proposal: ...


def cassette_key(prompt: str) -> str:
    """Stable content hash. Same question always hits the same recording."""
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]


def build_prompt(locator_key: str, description: str, candidates: list[dict]) -> str:
    """Prune candidates before sending.

    A trimmed candidate list is cheaper, faster and more stable than raw page
    source, and it keeps the cassette key stable across irrelevant page changes.
    """
    trimmed = [
        {
            "tag": c.get("tag"),
            "name": (c.get("accessible_name") or "")[:60],
            "text": (c.get("text") or "")[:60],
            "attrs": {
                k: v for k, v in (c.get("attrs") or {}).items()
                # Ground-truth markers are stripped here as well as at extraction
                # time. Redundant by design, and asserted by a unit test.
                if not k.startswith("data-bantay")
            },
        }
        for c in candidates[:40]
    ]
    return json.dumps(
        {"looking_for": {"key": locator_key, "description": description},
         "page_elements": trimmed},
        sort_keys=True,
    )


def _parse(text: str, source: str) -> Proposal:
    """Tolerate fences and stray prose; refuse to invent a result."""
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        return Proposal.empty(source, "model returned no parseable JSON object")
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        return Proposal.empty(source, f"invalid JSON from model: {exc}")
    selector = data.get("selector")
    return Proposal(
        selector=selector if isinstance(selector, str) and selector.strip() else None,
        confidence=float(data.get("confidence") or 0.0),
        reason=str(data.get("reason") or "")[:200],
        source=source,
    )


class DisabledProposer:
    """The default. Keeps the suite deterministic and credential-free."""

    def propose(self, prompt: str) -> Proposal:
        return Proposal.empty("disabled", "AI assist disabled (BANTAY_AI unset)")


class CassetteProposer:
    """Replay recorded responses; optionally record new ones."""

    def __init__(self, directory: Path = CASSETTE_DIR, record_with: LocatorProposer | None = None):
        self.directory = Path(directory)
        self.record_with = record_with

    def _path(self, prompt: str) -> Path:
        return self.directory / f"{cassette_key(prompt)}.json"

    def propose(self, prompt: str) -> Proposal:
        path = self._path(prompt)
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return Proposal(
                selector=data.get("selector"),
                confidence=float(data.get("confidence") or 0.0),
                reason=data.get("reason", ""),
                source="cassette",
            )
        if self.record_with is None:
            return Proposal.empty("cassette", f"no cassette for prompt {cassette_key(prompt)}")
        fresh = self.record_with.propose(prompt)
        self.directory.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {"selector": fresh.selector, "confidence": fresh.confidence,
                 "reason": fresh.reason, "prompt": json.loads(prompt)},
                indent=2, sort_keys=True,
            ),
            encoding="utf-8",
        )
        return fresh


class AnthropicProposer:
    """Live calls. Only reachable with BANTAY_AI=live and a key present."""

    def __init__(self, model: str = "claude-sonnet-4-6", max_tokens: int = 300):
        self.model = model
        self.max_tokens = max_tokens

    def propose(self, prompt: str) -> Proposal:
        try:
            import anthropic
        except ImportError:
            return Proposal.empty("live", "anthropic SDK not installed")
        if not os.environ.get("ANTHROPIC_API_KEY"):
            return Proposal.empty("live", "ANTHROPIC_API_KEY not set")
        try:
            client = anthropic.Anthropic()
            response = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text")
        except Exception as exc:  # network, rate limit or auth must not fail a test
            return Proposal.empty("live", f"call failed: {type(exc).__name__}: {exc}")
        return _parse(text, "live")


def build_proposer(mode: str | None = None) -> LocatorProposer:
    """Factory driven by `BANTAY_AI`: unset/off | replay | record | live."""
    mode = (mode or os.environ.get("BANTAY_AI") or "off").strip().lower()
    if mode in ("off", "", "disabled"):
        return DisabledProposer()
    if mode == "replay":
        return CassetteProposer()
    if mode == "record":
        return CassetteProposer(record_with=AnthropicProposer())
    if mode == "live":
        return AnthropicProposer()
    raise ValueError(f"Unknown BANTAY_AI mode: {mode!r} (off|replay|record|live)")
