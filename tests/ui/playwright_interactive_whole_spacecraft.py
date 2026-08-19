"""Playwright smoke test for the whole-spacecraft interactive workbench.

Run against an already-started feature-enabled server, for example::

    SAT_SIM_INTERACTIVE_ENABLED=1 sat-agent serve --port 8765
    SAT_SIM_UI_URL=http://127.0.0.1:8765 python3 tests/ui/playwright_interactive_whole_spacecraft.py
"""
from __future__ import annotations

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


BASE_URL = os.environ.get("SAT_SIM_UI_URL", "http://127.0.0.1:8765")
SCREENSHOT_PATH = Path(os.environ.get("SAT_SIM_UI_SCREENSHOT", "/tmp/sat-interactive-whole-spacecraft.png"))


def main() -> None:
    browser_errors: list[str] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda error: browser_errors.append(str(error)))
        page.goto(BASE_URL, wait_until="networkidle")

        nav = page.locator("#interactiveNavButton")
        expect(nav).to_be_visible()
        nav.click()
        expect(page.locator("#interactiveView h1")).to_have_text("整星实时交互")
        expect(page.locator("#interactiveCapability")).to_have_value("whole_spacecraft.unified_native.v1")
        expect(page.locator("#interactiveCapability option:checked")).to_have_text("整星（推荐）")

        # The unstarted default whole-spacecraft scope exposes every supported
        # control target instead of collapsing to a single whole-spacecraft TC.
        expect(page.locator("#interactiveCommandTarget option")).to_have_count(4)
        expect(page.locator("#interactiveCommandTarget")).to_contain_text("姿轨控分系统")
        expect(page.locator("#interactiveCommandTarget")).to_contain_text("能源分系统")
        expect(page.locator("#interactiveCommandTarget")).to_contain_text("通信数传分系统")

        page.locator("#interactiveDuration").fill("10")
        page.locator("#interactiveQuantum").select_option("0.5")
        page.locator("#createInteractiveSessionBtn").click()
        expect(page.locator("#interactiveState")).to_have_text("READY", timeout=60_000)

        expect(page.locator("#interactiveTelemetryObject option")).to_have_count(8)
        page.locator("#interactiveTelemetryObject").select_option("eps")
        expect(page.locator("#interactiveTelemetryObject option:checked")).to_have_text("能源")

        page.locator("#interactiveCommandTarget").select_option("subsystem.eps")
        expect(page.locator("#interactiveCommandOperation")).to_contain_text("负载开关")
        expect(page.locator("#interactiveCommandOperation")).to_contain_text("能源保护开关")
        page.locator("#interactiveCommandOperation").select_option("eps.load.set")
        expect(page.locator("#interactiveCommandForm [data-parameter='load_id']")).to_be_visible()
        expect(page.locator("#interactiveCommandForm [data-parameter='enabled']")).to_be_visible()

        page.locator("#interactiveStartBtn").click()
        expect(page.locator("#interactiveState")).to_have_text("RUNNING")
        page.locator("#interactivePauseBtn").click()
        expect(page.locator("#interactiveState")).to_have_text("PAUSED")
        before_step = float(page.locator("#interactiveSimTime").inner_text().split()[0])
        page.locator("#interactiveStepBtn").click()
        expect(page.locator("#interactiveSimTime")).not_to_have_text(f"{before_step:.3f} s")

        # Queue an EPS command inside the same whole-spacecraft session and
        # advance one quantum so the unified runtime applies it.
        page.locator("#interactiveCommandForm [data-parameter='load_id']").select_option("payload")
        page.locator("#interactiveCommandForm [data-parameter='enabled']").uncheck()
        page.locator("#interactiveSendCommandBtn").click()
        expect(page.locator("#interactiveAckList")).to_contain_text("QUEUED")
        page.locator("#interactiveStepBtn").click()
        expect(page.locator("#interactiveAckList")).to_contain_text("ACKED")
        # Preserve one complete post-ACK telemetry quantum so evidence sealing
        # has an observable effect window when the operator stops the session.
        ack_time = float(page.locator("#interactiveSimTime").inner_text().split()[0])
        page.locator("#interactiveStepBtn").click()
        expect(page.locator("#interactiveSimTime")).not_to_have_text(f"{ack_time:.3f} s")

        SCREENSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SCREENSHOT_PATH), full_page=True)
        page.on("dialog", lambda dialog: dialog.accept())
        page.locator("#interactiveStopBtn").click()
        expect(page.locator("#interactiveState")).to_have_text("COMPLETED", timeout=30_000)
        browser.close()

    assert not browser_errors, f"browser page errors: {browser_errors}"
    print(f"PASS whole-spacecraft interactive UI; screenshot={SCREENSHOT_PATH}")


if __name__ == "__main__":
    main()
