"""Capture the visible publisher guides in ordinary Chrome for offline verification."""

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from playwright.sync_api import Error, sync_playwright

ROOT = Path(__file__).resolve().parent


def main():
    manifest = json.loads((ROOT / "data_sources" / "corpus_manifest.json").read_text(encoding="utf-8"))
    output = ROOT / "local_html" / "expanded_guides.json"
    captures = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page()
        for guide in manifest["guides"]:
            url = guide["url"]
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=14000)
                if response is None or response.status != 200 or page.url != url:
                    print(f"Skipped {guide['key']}: unexpected HTTP response or URL")
                    continue
                heading = page.locator("h1").first.inner_text(timeout=4000)
                if re.search(r"just a moment|access denied|captcha", heading, re.IGNORECASE):
                    print(f"Skipped {guide['key']}: challenge")
                    continue
                article = page.locator(".entry-content, .jeg_post_content, article").first
                captures[guide["key"]] = {
                    "url": url,
                    "heading": heading,
                    "text": article.inner_text(timeout=5000),
                    "sections": article.locator("h2,h3,h4").all_inner_texts(),
                    "captured": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                print(f"Captured {guide['key']}")
            except Error as error:
                print(f"Skipped {guide['key']}: {type(error).__name__}")
        browser.close()
    if not captures:
        raise SystemExit("No guides rendered. Existing capture was preserved.")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(captures, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(captures)} local guide captures to {output}")


if __name__ == "__main__":
    main()
