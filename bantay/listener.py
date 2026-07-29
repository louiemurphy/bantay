"""Robot Framework listener (API v3).

Cross-cutting concerns are handled here rather than repeated in suite teardowns,
so they apply uniformly to every suite in a run and cannot be forgotten in one.

The listener does three things:

1. Captures a screenshot and the page URL on failure.
2. Writes `reports/telemetry.json`: one machine-readable record per test,
   including resolution tiers. Robot's own output.xml suits human review; this
   file is for dashboards and trend analysis.
3. Tags tests by outcome so `--include` / `--exclude` can act on them in a later
   run, for example re-running only the tests that healed.

Enable with:  robot --listener bantay.listener.BantayListener tests/
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from robot.libraries.BuiltIn import BuiltIn

REPORT_DIR = Path(os.environ.get("BANTAY_REPORTS", "reports"))


class BantayListener:
    ROBOT_LISTENER_API_VERSION = 3

    def __init__(self, screenshots: str = "on-failure", out: str | Path = REPORT_DIR):
        self.screenshots = screenshots
        self.out = Path(out)
        self.records: list[dict] = []
        self._started: float = 0.0

    # -- lifecycle -------------------------------------------------------

    def start_test(self, data, result) -> None:
        self._started = time.time()

    def end_test(self, data, result) -> None:
        record = {
            "suite": getattr(data.parent, "name", ""),
            "test": result.name,
            "status": result.status,
            "duration_s": round(time.time() - self._started, 3),
            "tags": list(result.tags),
            "message": (result.message or "")[:500],
        }

        stats = self._resolution_stats()
        if stats:
            record["resolution_tiers"] = stats
            recovered = sum(v for k, v in stats.items() if k in ("FALLBACK", "SCORED", "ASSISTED"))
            if recovered:
                # Surfaced as a tag so the next run can target these directly.
                result.tags.add("healed")
                record["healed_count"] = recovered

        if result.status == "FAIL":
            shot = self._capture_screenshot(result.name)
            if shot:
                record["screenshot"] = str(shot)
            url = self._current_url()
            if url:
                record["url_at_failure"] = url

        self.records.append(record)

    def close(self) -> None:
        """Write telemetry once, at the end of the run."""
        self.out.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "totals": {
                "tests": len(self.records),
                "failed": sum(1 for r in self.records if r["status"] == "FAIL"),
                "healed": sum(1 for r in self.records if r.get("healed_count")),
            },
            "tests": self.records,
        }
        (self.out / "telemetry.json").write_text(
            json.dumps(payload, indent=2), encoding="utf-8"
        )

    # -- helpers ---------------------------------------------------------

    def _selenium(self):
        try:
            return BuiltIn().get_library_instance("SeleniumLibrary")
        except Exception:
            return None

    def _resolution_stats(self) -> dict | None:
        try:
            bantay = BuiltIn().get_library_instance("bantay.BantayLibrary")
        except Exception:
            return None
        try:
            stats = bantay.get_resolution_stats()
        except Exception:
            return None
        return {k: v for k, v in stats.items() if v}

    def _current_url(self) -> str | None:
        selenium = self._selenium()
        try:
            return selenium.driver.current_url if selenium else None
        except Exception:
            return None

    def _capture_screenshot(self, test_name: str) -> Path | None:
        if self.screenshots == "off":
            return None
        selenium = self._selenium()
        if selenium is None:
            return None
        shots = self.out / "screenshots"
        shots.mkdir(parents=True, exist_ok=True)
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in test_name)[:80]
        path = shots / f"{safe}-{time.strftime('%H%M%S')}.png"
        try:
            selenium.driver.save_screenshot(str(path))
        except Exception:
            return None
        return path
