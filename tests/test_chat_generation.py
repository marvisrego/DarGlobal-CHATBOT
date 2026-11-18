import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from app.chat.answers import Evidence
from app.chat.openrouter_provider import GeneratedAnswer, ModelUnavailable, generated_answer, http_error_category


class ChatGenerationTests(unittest.TestCase):
    @staticmethod
    def completion(content, finish="stop"):
        return httpx.Response(200, json={
            "model": "provider/model",
            "choices": [{"finish_reason": finish, "message": {"content": content}}],
        })

    def test_response_uses_provider_text_and_labels_failures_and_abstentions(self):
        with patch.dict(os.environ, CHAT_MODE="mock", PROPERTY_DATA_MODE="live"):
            import main
        rendered = "- Tierra Viva offers villas.\n- Les Vagues includes retail."
        provider = AsyncMock()
        provider.generate.side_effect = [
            GeneratedAnswer(rendered, "provider/returned-model"), ModelUnavailable("rate_limited"),
        ]
        question = "Which has villas, Tierra Viva or Les Vagues?"
        with (
            patch.object(main, "DATA_MODE", "live"),
            patch.object(main, "vector_lookup", None),
            patch.object(main, "answer_provider", provider),
            self.assertLogs("uvicorn.error.darglobal") as logs,
            TestClient(main.app) as client,
        ):
            comparison = client.post("/api/chat", json={"message": question})
            guide = client.post("/api/chat", json={
                "message": "What documents does Wasalt recommend checking before buying a home?",
            }).json()
            price = client.post("/api/chat", json={"message": "What is the current asking price of AIDA?"}).json()
        answer = comparison.json()
        self.assertEqual(answer["answer"], rendered)
        self.assertEqual(answer["inference_model"], "provider/returned-model")
        self.assertEqual(answer["answer_path"], "openrouter")
        self.assertEqual(comparison.headers["x-request-id"], answer["request_id"])
        self.assertIn(answer["request_id"], logs.output[0])
        self.assertNotIn(question, logs.output[0])
        self.assertEqual(guide["answer_path"], "source_summary")
        self.assertTrue(guide["answer"].startswith("Source summary"))
        self.assertIsNone(guide["inference_model"])
        self.assertEqual([source["title"] for source in guide["sources"]], ["Step 6: Conduct Proper Due Diligence"])
        self.assertIn("error_category=rate_limited", logs.output[1])
        self.assertEqual(price["answer_path"], "abstention")
        self.assertIn("no verified asking price", price["answer"])
        self.assertEqual(provider.generate.await_count, 2)

    def test_truncated_generation_is_rejected(self):
        with self.assertRaises(ModelUnavailable) as failure:
            generated_answer(self.completion("partial text", finish="length"), [])
        self.assertEqual(failure.exception.category, "incomplete_response")

    def test_generation_renders_only_selected_claims(self):
        evidence = [
            Evidence("Example project", "Developer", ("Property type: Villas", "Completed")),
            Evidence("Budget guide", "Guide publisher", ("Include maintenance in the budget.",), is_guide=True),
        ]
        answer = generated_answer(self.completion("[[0],[0]]"), evidence)
        self.assertEqual(answer.text, "- Example project — Property type: Villas.\n"
                                     "- Guide publisher — Include maintenance in the budget.")
        self.assertEqual(answer.model, "provider/model")

    def test_novel_prose_and_invalid_claim_references_are_rejected(self):
        evidence = [Evidence("Guide", "Publisher", ("Read the sales contract.",), is_guide=True)]
        for content in (
            "Read the sales contract and hire an adviser.",
            '[[0,"hire an adviser"]]',
            "[[1]]",
            "[[true]]",
            "[[0,0]]",
            "[[0],[]]",
        ):
            with self.subTest(content=content), self.assertRaises(ModelUnavailable) as failure:
                generated_answer(self.completion(content), evidence)
            self.assertEqual(failure.exception.category, "invalid_selection")

    def test_daily_limit_is_distinct_from_transient_rate_limit(self):
        exhausted = httpx.Response(429, json={
            "error": {"metadata": {"limit_source": "openrouter_free_tier_daily"}},
        })
        self.assertEqual(http_error_category(exhausted), "free_tier_daily_limit")
        self.assertEqual(http_error_category(httpx.Response(429)), "rate_limited")
