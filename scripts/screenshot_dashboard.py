#!/usr/bin/env python3
"""Render docs/dashboard.png from the dashboard served by scripts/mock_api.py (needs Playwright)."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = 8765


def main() -> int:
    from playwright.sync_api import sync_playwright

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "scripts" / "mock_api.py"), "--port", str(PORT)]
    )
    try:
        time.sleep(1.5)
        with sync_playwright() as p:
            kwargs = {}
            if os.environ.get("CHROMIUM_PATH"):
                kwargs["executable_path"] = os.environ["CHROMIUM_PATH"]
            browser = p.chromium.launch(**kwargs)
            page = browser.new_page(viewport={"width": 1000, "height": 760}, device_scale_factor=2)
            page.goto(f"http://127.0.0.1:{PORT}/")
            page.wait_for_selector("tr.row")
            page.click("tr.row[data-id='hive-1C5A00']")
            page.wait_for_function("document.querySelectorAll('#chart path').length >= 2")
            time.sleep(0.5)
            out = ROOT / "docs" / "dashboard.png"
            page.screenshot(path=str(out), full_page=True)
            browser.close()
            print(f"wrote {out}")
    finally:
        server.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
