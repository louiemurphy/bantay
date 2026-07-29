"""DOM snapshot model.

An `ElementSnapshot` is a browser-independent description of a single element.
Two things produce them:

* `EXTRACT_CANDIDATES_JS`, which runs in a real browser via Selenium.
* Hand-built dictionaries, used by the unit tests.

Keeping that boundary explicit is what allows the scoring logic to be tested
without a browser or network access.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Attributes that usually survive a redesign and are therefore worth
# fingerprinting. `class` is excluded: class churn is one of the most common
# causes of locator rot, so classes are scored separately at a low weight.
STABLE_ATTRS = (
    "id",
    "name",
    "type",
    "role",
    "href",
    "placeholder",
    "value",
    "alt",
    "title",
    "aria-label",
    "aria-labelledby",
    "data-test",
    "data-testid",
    "data-qa",
)

_WS = re.compile(r"\s+")


def normalize_text(value: str | None) -> str:
    """Collapse whitespace and casefold. Used everywhere before comparison."""
    if not value:
        return ""
    return _WS.sub(" ", value).strip().casefold()


def tokens(value: str | None) -> set[str]:
    """Split into comparable word tokens, dropping single characters."""
    return {t for t in re.split(r"[^a-z0-9]+", normalize_text(value)) if len(t) > 1}


@dataclass(frozen=True)
class ElementSnapshot:
    """A structural + semantic fingerprint of one element."""

    tag: str
    text: str = ""
    accessible_name: str = ""
    attrs: dict[str, str] = field(default_factory=dict)
    classes: tuple[str, ...] = ()
    # Ancestor tag chain from the document root down to the parent, e.g.
    # ("html", "body", "div", "form"). Compared by common suffix so that markup
    # inserted near the root costs less than a changed immediate parent.
    ancestor_path: tuple[str, ...] = ()
    sibling_index: int = 0
    # Absolute XPath, used only to re-find an element after a heal. Never
    # scored, since it is the most brittle field on this object.
    xpath: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ElementSnapshot":
        attrs = {k: str(v) for k, v in (raw.get("attrs") or {}).items() if v is not None}
        return cls(
            tag=str(raw.get("tag", "")).lower(),
            text=normalize_text(raw.get("text")),
            accessible_name=normalize_text(raw.get("accessible_name")),
            attrs=attrs,
            classes=tuple(sorted(raw.get("classes") or ())),
            ancestor_path=tuple(raw.get("ancestor_path") or ()),
            sibling_index=int(raw.get("sibling_index") or 0),
            xpath=str(raw.get("xpath") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "text": self.text,
            "accessible_name": self.accessible_name,
            "attrs": dict(self.attrs),
            "classes": list(self.classes),
            "ancestor_path": list(self.ancestor_path),
            "sibling_index": self.sibling_index,
            "xpath": self.xpath,
        }


# Harvests every plausibly interactive element in a single round trip. One
# script rather than many WebDriver calls keeps resolution fast.
EXTRACT_CANDIDATES_JS = """
const STABLE = %s;
const SELECTOR = 'a,button,input,select,textarea,label,[role],[onclick],' +
                 '[data-test],[data-testid],[data-qa],summary,option';

function accName(el) {
  const aria = el.getAttribute('aria-label');
  if (aria) return aria;
  const labelledby = el.getAttribute('aria-labelledby');
  if (labelledby) {
    const parts = labelledby.split(/\\s+/)
      .map(id => document.getElementById(id))
      .filter(Boolean)
      .map(n => n.textContent);
    if (parts.length) return parts.join(' ');
  }
  if (el.id) {
    const lab = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
    if (lab) return lab.textContent;
  }
  const wrapping = el.closest('label');
  if (wrapping) return wrapping.textContent;
  return el.getAttribute('placeholder') || el.getAttribute('title') ||
         el.getAttribute('alt') || el.value || el.textContent || '';
}

function xpathOf(el) {
  const parts = [];
  for (let n = el; n && n.nodeType === 1; n = n.parentNode) {
    let i = 1;
    for (let s = n.previousSibling; s; s = s.previousSibling) {
      if (s.nodeType === 1 && s.nodeName === n.nodeName) i++;
    }
    parts.unshift(n.nodeName.toLowerCase() + '[' + i + ']');
  }
  return '/' + parts.join('/');
}

function visible(el) {
  const r = el.getBoundingClientRect();
  if (r.width === 0 && r.height === 0) return false;
  const s = window.getComputedStyle(el);
  return s.visibility !== 'hidden' && s.display !== 'none';
}

return Array.from(document.querySelectorAll(SELECTOR))
  .filter(visible)
  .map(el => {
    const attrs = {};
    for (const a of STABLE) {
      const v = el.getAttribute(a);
      if (v !== null) attrs[a] = v;
    }
    const path = [];
    for (let p = el.parentElement; p; p = p.parentElement) {
      path.unshift(p.nodeName.toLowerCase());
    }
    return {
      tag: el.nodeName.toLowerCase(),
      text: (el.textContent || '').slice(0, 200),
      accessible_name: (accName(el) || '').slice(0, 200),
      attrs: attrs,
      classes: Array.from(el.classList),
      ancestor_path: path,
      sibling_index: Array.from(el.parentElement ? el.parentElement.children : [])
        .indexOf(el),
      xpath: xpathOf(el)
    };
  });
""" % list(STABLE_ATTRS)
