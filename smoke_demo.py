"""Check the deployed demo from a fresh Chrome session."""

import argparse
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from answers import DAR_REPORT_CITATION, WASALT_GUIDE_URL

REPORT_SHA256 = "39426ee0815355f2f76aa68e31394194269a38b1be3886a90ab6fef16803be0a"
COMPARISON = "Compare Tierra Viva and Les Vagues across location and property type."
GUIDE = "What does Wasalt's buyer guide suggest checking before making an offer?"
NO_PRICE = "What is the current asking price of AIDA?"


def ask(page, question):
    page.get_by_role("button", name="New chat").click()
    page.get_by_role("textbox", name="Your question").fill(question)
    with page.expect_response(lambda r: r.url.endswith("/api/chat") and r.request.method == "POST",
                              timeout=50000) as response:
        page.get_by_role("button", name="Send question").click()
    answer = response.value
    assert answer.status == 200, answer.json()
    data = answer.json()
    assert data["inference_model"], "Expected live OpenRouter inference"
    expect(page.locator(".reply p").first).to_contain_text("Based on the cited sources.")
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    parts = urlsplit(base)
    if parts.scheme != "https" and parts.hostname not in {"localhost", "127.0.0.1"}:
        parser.error("use an HTTPS URL for a public service")

    with TemporaryDirectory(prefix="darglobal-smoke-") as profile, sync_playwright() as playwright:
        preferences = Path(profile) / "Default" / "Preferences"
        preferences.parent.mkdir()
        preferences.write_text(json.dumps({"plugins": {"always_open_pdf_externally": True}}), encoding="utf-8")
        context = playwright.chromium.launch_persistent_context(
            profile, channel="chrome", headless=False, accept_downloads=True
        )
        try:
            health = context.request.get(base + "/health", timeout=75000)
            assert health.status == 200 and health.json() == {"status": "ok"}
            page = context.pages[0]
            navigation = page.goto(base, wait_until="domcontentloaded", timeout=75000)
            assert navigation.status == 200
            expect(page.get_by_text("OpenRouter: Real corpus", exact=True)).to_be_visible()
            examples = page.locator("[data-question]").all_inner_texts()
            assert examples == [COMPARISON, GUIDE, "Summarize AIDA and add Wasalt's guidance on off-plan risk."]
            comparison = ask(page, COMPARISON)
            for text in ("Benahavís, Spain", "Lusail, Qatar", "2026-06-30"):
                assert text in comparison["answer"], text
            assert {row["url"] for row in comparison["sources"]} == {
                DAR_REPORT_CITATION.replace("#page=24", "#page=25"),
                DAR_REPORT_CITATION.replace("#page=24", "#page=26"),
            }
            assert {row["publisher"] for row in comparison["sources"]} == {"Dar Global PLC"}
            with page.expect_download(timeout=25000) as download:
                page.get_by_role("link", name="Tierra Viva", exact=True).click()
            assert hashlib.sha256(download.value.path().read_bytes()).hexdigest() == REPORT_SHA256
            print("DarGlobal comparison, dated facts, and PDF citation passed.")

            guide = ask(page, GUIDE)
            for text in ("title deed", "building permit", "completion certificate"):
                assert text in guide["answer"], text
            assert {row["url"] for row in guide["sources"]} == {WASALT_GUIDE_URL}
            assert {row["publisher"] for row in guide["sources"]} == {"Wasalt"}
            with page.expect_navigation(wait_until="domcontentloaded", timeout=25000) as destination:
                page.locator(".sources a").first.click()
            assert destination.value.status == 200 and page.url == WASALT_GUIDE_URL
            expect(page.get_by_role("heading", name="How to Buy a House in Saudi Arabia", exact=False).first).to_be_visible()
            print("Wasalt guide facts and article citation passed.")

            page.goto(base, wait_until="domcontentloaded")
            missing = ask(page, NO_PRICE)
            assert "no verified asking price" in missing["answer"]
            assert "2026-06-30" in missing["answer"] and "SAR" not in missing["answer"]
            assert {row["publisher"] for row in missing["sources"]} == {"Dar Global PLC", "OMRAN Group"}
            print("Missing price and associated source citations passed.")
        finally:
            context.close()


if __name__ == "__main__":
    main()
