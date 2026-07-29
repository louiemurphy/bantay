"""Bantay: a locator-resilience layer for Robot Framework.

"Bantay" is Filipino for watchman. The framework recovers from locator rot
deterministically and reports how each element was found, so that recovery is
visible rather than silent. Language models may propose candidate selectors but
never decide a pass/fail outcome.

Public surface:

    from bantay import BantayLibrary            # Robot Framework keyword library
    from bantay.listener import BantayListener  # telemetry and screenshots
"""

from .keywords import BantayLibrary

__version__ = "0.1.0"
__all__ = ["BantayLibrary", "__version__"]
