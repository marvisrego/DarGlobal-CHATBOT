"""Capture the visible publisher guides in ordinary Chrome for offline verification."""

import argparse
from datetime import datetime, timezone
import json
import re

from playwright.sync_api import Error, sync_playwright

from corpus_snapshot import ROOT


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expansion", action="store_true", help="Capture the v2 project and guide sources")
    args = parser.parse_args()
    manifest_name = "corpus_expansion.json" if args.expansion else "corpus_manifest.json"
    manifest = json.loads((ROOT / "data_sources" / manifest_name).read_text(encoding="utf-8"))
    output = ROOT / "local_html" / ("expansion-capture.json" if args.expansion else "expanded_guides.json")
    sources = manifest["projects"] + manifest["guides"] if args.expansion else manifest["guides"]
    captures = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=False)
        page = browser.new_page()
        for guide in sources:
            url = guide["url"]
            key = url if args.expansion else guide["key"]
            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=14000)
                if response is None or response.status != 200 or page.url != url:
                    print(f"Skipped {key}: unexpected HTTP response or URL")
                    continue
                if args.expansion:
                    page.locator("body").get_by_text(
                        guide["evidence"][-1] if "evidence" in guide else guide["sections"][0]["section"],
                        exact=False,
                    ).first.wait_for(timeout=10000)
                    title = page.title()
                    if re.search(r"just a moment|access denied|captcha", title, re.IGNORECASE):
                        print(f"Skipped {key}: challenge")
                        continue
                    captures[key] = {
                        "url": url, "status": response.status, "title": title,
                        "text": page.locator("body").inner_text(timeout=5000),
                        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    }
                    print(f"Captured {key}")
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
                print(f"Skipped {key}: {type(error).__name__}")
        browser.close()
    if not captures or (args.expansion and len(captures) != len(sources)):
        raise SystemExit("Required pages did not render. Existing capture was preserved.")
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps(captures, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(captures)} local guide captures to {output}")


if __name__ == "__main__":
    main()
