"""An unknown locator key must suggest the key the author meant.

A typo in a key is a different class of problem from a page change: nothing has
drifted, nothing needs healing, and the fix is one character. The error message is
the entire remedy, so it has to name the intended key.
"""

from __future__ import annotations

import pytest

from bantay.registry import Locator, LocatorNotFound, LocatorRegistry


def registry(*keys: str) -> LocatorRegistry:
    return LocatorRegistry({k: Locator(key=k) for k in keys})


class TestSuggestions:
    def test_transposition_is_suggested(self):
        """The regression: substring matching cannot catch a letter swap."""
        with pytest.raises(LocatorNotFound) as exc:
            registry("email", "postcode").get("emial")
        message = str(exc.value)
        assert "Did you mean" in message
        assert "email" in message

    def test_substring_still_works(self):
        with pytest.raises(LocatorNotFound) as exc:
            registry("checkout.place_order").get("place_order")
        assert "checkout.place_order" in str(exc.value)

    def test_namespaced_keys_match_on_leaf(self):
        with pytest.raises(LocatorNotFound) as exc:
            registry("checkout.email").get("checkout.emial")
        assert "checkout.email" in str(exc.value)

    def test_missing_letter_is_suggested(self):
        with pytest.raises(LocatorNotFound) as exc:
            registry("confirmation").get("confirmaton")
        assert "confirmation" in str(exc.value)

    def test_nothing_similar_gives_no_hint(self):
        """A suggestion that fires on anything is noise, so no hint is correct."""
        with pytest.raises(LocatorNotFound) as exc:
            registry("email", "postcode").get("zzzzzzzz")
        assert "Did you mean" not in str(exc.value)

    def test_message_always_names_the_key_asked_for(self):
        with pytest.raises(LocatorNotFound) as exc:
            registry("email").get("emial")
        assert "emial" in str(exc.value)
