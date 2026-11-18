import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from openrouter_provider import GeneratedAnswer, ModelUnavailable, generated_answer


class ChatGenerationTests(unittest.TestCase):
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
        response = httpx.Response(200, json={
            "model": "provider/model",
            "choices": [{"finish_reason": "length", "message": {"content": "partial text"}}],
        })
        with self.assertRaises(ModelUnavailable) as failure:
            generated_answer(response)
        self.assertEqual(failure.exception.category, "incomplete_response")
