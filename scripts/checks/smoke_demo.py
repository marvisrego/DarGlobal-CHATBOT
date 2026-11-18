"""Check the deployed demo from a fresh Chrome session."""

import argparse
import hashlib
import json
from pathlib import Path
import re
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from playwright.sync_api import expect, sync_playwright

from app.chat.answers import DAR_REPORT_CITATION, WASALT_GUIDE_URL

REPORT_SHA256 = "39426ee0815355f2f76aa68e31394194269a38b1be3886a90ab6fef16803be0a"
COMPARISON = ("At Tierra Viva and Les Vagues, which one offers villas, and which one includes retail? "
              "Answer in exactly two bullets.")
GUIDE = "What documents does Wasalt recommend checking before buying a home?"
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
    assert data["mode"] == "live"
    assert re.fullmatch(r"[0-9a-f]{32}", data["request_id"])
    assert answer.headers.get("x-request-id") == data["request_id"]
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_url")
    parser.add_argument("--commit", help="Require this commit SHA or prefix in the health response header")
    parser.add_argument("--provider", choices=("openrouter", "nvidia"), default="openrouter")
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
            commit = health.headers.get("x-deploy-commit", "")
            if args.commit:
                assert commit.startswith(args.commit), f"Expected deployed commit {args.commit}, got {commit or 'no header'}"
            print(f"Health passed. Commit: {commit or '(local)'}. Retrieval mode: "
                  f"{health.headers.get('x-retrieval-mode', 'unknown')}.")
            page = context.pages[0]
            navigation = page.goto(base, wait_until="domcontentloaded", timeout=75000)
            assert navigation.status == 200
            provider_label = "NVIDIA" if args.provider == "nvidia" else "OpenRouter"
            expect(page.get_by_text(f"{provider_label}: Real corpus", exact=True)).to_be_visible()
            examples = page.locator("[data-question]").all_inner_texts()
            assert COMPARISON not in examples and GUIDE not in examples
            comparison = ask(page, COMPARISON)
            assert comparison["answer_path"] == args.provider and comparison["inference_model"], comparison["answer_path"]
            if args.provider == "nvidia":
                assert comparison["inference_provider"] == "nvidia"
                expect(page.locator(".reply .inference")).to_have_text(f"NVIDIA · {comparison['inference_model']}")
            bullets = [re.fullmatch(r"[-*•]\s+(.+)", line) for line in comparison["answer"].splitlines() if line.strip()]
            assert len(bullets) == 2 and all(bullets), comparison["answer"]
            items = [bullet[1] for bullet in bullets]
            assert any("tierra viva" in item.casefold() and "villas" in item.casefold() for item in items)
            assert any("les vagues" in item.casefold() and "retail" in item.casefold() for item in items)
            expect(page.locator(".reply .answer-list li")).to_have_text(items)
            assert {row["url"] for row in comparison["sources"]} == {
                DAR_REPORT_CITATION.replace("#page=24", "#page=25"),
                DAR_REPORT_CITATION.replace("#page=24", "#page=26"),
            }
            assert {row["publisher"] for row in comparison["sources"]} == {"Dar Global PLC"}
            with page.expect_download(timeout=25000) as download:
                page.get_by_role("link", name="Tierra Viva", exact=True).click()
            assert hashlib.sha256(download.value.path().read_bytes()).hexdigest() == REPORT_SHA256
            print(f"Novel two-bullet answer and PDF citation passed. Model: {comparison['inference_model']}. "
                  f"Request: {comparison['request_id']}.")

            guide = ask(page, GUIDE)
            for text in ("title deed", "building permit", "completion certificate"):
                assert text in guide["answer"].casefold(), text
            assert {row["url"] for row in guide["sources"]} == {WASALT_GUIDE_URL}
            assert {row["publisher"] for row in guide["sources"]} == {"Wasalt"}
            assert {row["title"] for row in guide["sources"]} == {"Step 6: Conduct Proper Due Diligence"}
            expect(page.locator(".reply")).to_contain_text("title deed", ignore_case=True)
            with page.expect_navigation(wait_until="domcontentloaded", timeout=25000) as destination:
                page.locator(".sources a").first.click()
            assert destination.value.status == 200 and page.url == WASALT_GUIDE_URL
            expect(page.get_by_role("heading", name="How to Buy a House in Saudi Arabia", exact=False).first).to_be_visible()
            print(f"Wasalt paraphrase and due-diligence citation passed. Retrieval: {guide['retrieval_path']}. "
                  f"Answer: {guide['answer_path']}. Model: {guide['inference_model']}. Request: {guide['request_id']}.")

            page.goto(base, wait_until="domcontentloaded")
            missing = ask(page, NO_PRICE)
            assert missing["answer_path"] == "abstention" and missing["inference_model"] is None
            assert "no verified asking price" in missing["answer"]
            assert "2026-06-30" in missing["answer"] and "SAR" not in missing["answer"]
            assert {row["publisher"] for row in missing["sources"]} == {"Dar Global PLC", "OMRAN Group"}
            print(f"Missing price abstention and source citations passed. Request: {missing['request_id']}.")
        finally:
            context.close()


if __name__ == "__main__":
    main()
