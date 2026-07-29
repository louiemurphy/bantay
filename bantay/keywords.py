"""BantayLibrary: the Robot Framework surface of the resolver.

Sits alongside SeleniumLibrary rather than replacing it. SeleniumLibrary already
handles browser lifecycle, waiting and reporting, so this library contributes one
thing: turning a registry key into a live element, resiliently and with an
auditable outcome.

Keyword design conventions used here:

* Keywords read as sentences in a test rather than as function calls.
* Every keyword that can fail explains why in its failure message.
* Tests speak in registry keys; a raw WebElement is only returned when asked for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from robot.api import logger
from robot.api.deco import keyword, library
from robot.libraries.BuiltIn import BuiltIn

from .ai import build_proposer
from .registry import LocatorRegistry
from .resolver import DIRECT, LocatorResolver, Resolution


@library(scope="SUITE", version="0.1.0", auto_keywords=False)
class BantayLibrary:
    """Resilient element resolution for Robot Framework.

    = Setup =

    | Library | bantay.BantayLibrary | locators=resources/locators |

    = Outcome tiers =

    Every resolution is graded. ``DIRECT`` means a declared locator worked.
    Anything else means the registry is drifting away from the product, and is
    logged as a warning and counted in `Get Resolution Stats`.
    """

    def __init__(self, locators: str = "resources/locators", ai: str | None = None,
                 propose_patches: bool = True):
        self._locators_path = Path(locators)
        self._ai_mode = ai
        self._propose_patches = propose_patches
        self._registry: LocatorRegistry | None = None
        self._resolver: LocatorResolver | None = None

    # -- wiring 

    @property
    def registry(self) -> LocatorRegistry:
        if self._registry is None:
            path = self._locators_path
            self._registry = (
                LocatorRegistry.load_dir(path) if path.is_dir() else LocatorRegistry.load(path)
            )
            logger.info(f"Loaded {len(self._registry)} locators from {path}")
        return self._registry

    @property
    def resolver(self) -> LocatorResolver:
        if self._resolver is None:
            self._resolver = LocatorResolver(
                registry=self.registry,
                driver_factory=self._driver,
                proposer=build_proposer(self._ai_mode),
                propose_patches=self._propose_patches,
            )
        return self._resolver

    def _driver(self) -> Any:
        """Borrow SeleniumLibrary's driver rather than owning a second one."""
        selenium = BuiltIn().get_library_instance("SeleniumLibrary")
        return selenium.driver

    # -- keywords 

    @keyword("Resolve Element")
    def resolve_element(self, key: str) -> Any:
        """Return the WebElement registered as ``key``, healing if necessary.

        Escalates through declared strategies, then deterministic fingerprint
        scoring, then (if enabled) an AI proposal that must still pass scoring.
        Fails the test if every tier is exhausted.

        Example:
        | ${el} = | Resolve Element | checkout.place_order |
        """
        resolution = self.resolver.resolve(key)
        if resolution.tier != DIRECT:
            logger.warn(f"Locator drift - {resolution.summary()}")
        else:
            logger.info(resolution.summary())
        return resolution.element

    @keyword("Click Registered Element")
    def click_registered_element(self, key: str) -> None:
        """Click the element registered as ``key``."""
        self.resolve_element(key).click()

    @keyword("Type Into Registered Element")
    def type_into_registered_element(self, key: str, text: str, clear: bool = True) -> None:
        """Type ``text`` into the element registered as ``key``."""
        element = self.resolve_element(key)
        if clear:
            element.clear()
        element.send_keys(text)

    @keyword("Get Registered Element Text")
    def get_registered_element_text(self, key: str) -> str:
        """Return the visible text of the element registered as ``key``."""
        return self.resolve_element(key).text

    @keyword("Registered Element Should Be Visible")
    def registered_element_should_be_visible(self, key: str) -> None:
        """Fail unless the element registered as ``key`` is displayed."""
        element = self.resolve_element(key)
        if not element.is_displayed():
            raise AssertionError(
                f"'{key}' resolved but is not visible. It exists in the DOM and is "
                f"hidden, which is a different bug from it being absent."
            )

    @keyword("Resolution Tier Should Be")
    def resolution_tier_should_be(self, key: str, expected: str) -> None:
        """Assert how an element was found, not merely that it was.

        Lets a test require that a locator work directly, with no healing, which
        is what keeps drift visible instead of silent.

        | Resolution Tier Should Be | checkout.email | DIRECT |
        """
        resolution = self.resolver.resolve(key)
        if resolution.tier != expected.upper():
            raise AssertionError(
                f"'{key}' resolved via {resolution.tier}, expected {expected.upper()}. "
                f"{resolution.notes or 'The registry and the page have diverged.'}"
            )

    @keyword("Get Resolution Stats")
    def get_resolution_stats(self) -> dict:
        """Return a tier -> count mapping for the suite so far."""
        return self.resolver.stats()

    @keyword("No Locator Drift Should Have Occurred")
    def no_locator_drift_should_have_occurred(self) -> None:
        """Fail the suite if anything needed healing.

        Intended as a teardown on a primary regression suite. Healing mid-run is
        acceptable because it keeps the report complete, but it should not pass
        review unnoticed.
        """
        drifted = [r for r in self.resolver.history if r.tier != DIRECT]
        if drifted:
            detail = "\n".join(f"  - {r.summary()}" for r in drifted)
            raise AssertionError(
                f"{len(drifted)} locator(s) needed recovery during this suite:\n{detail}\n"
                f"Review reports/patches/ and either accept the patch or fix the product."
            )

    @keyword("Log Resolution History")
    def log_resolution_history(self) -> None:
        """Write every resolution to the Robot log, for post-run triage."""
        for resolution in self.resolver.history:
            logger.info(resolution.summary())

    @keyword("Registered Locator Keys")
    def registered_locator_keys(self) -> list[str]:
        """All keys in the registry. Useful for data-driven coverage checks."""
        return self.registry.keys()
