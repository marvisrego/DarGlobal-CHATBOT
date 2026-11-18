import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.search.hybrid_retrieval import HybridLookup
from app.search.property_search import search


class HybridRetrievalTests(unittest.TestCase):
    def test_exact_matches_and_listing_filters_bypass_vector_search(self):
        lookup = Mock()
        for question, expected in (
            ("Where is the project at Old Park Lane?", ["project:mulliner"]),
            ("Find Wasalt apartments in Riyadh up to SAR 1 million.", []),
            ("Which completed DarGlobal projects are in Dubai?", ["project:urban-oasis", "project:da-vinci"]),
        ):
            with self.subTest(question=question):
                result = search(question, live_data=True, vector_lookup=lookup)
                self.assertEqual(result.passage_ids, expected)
        lookup.assert_not_called()

    def test_hits_resolve_to_verified_records_and_citations(self):
        lookup = Mock(return_value=[("project:fake", 1.0), ("project:aida", 0.9), ("project:tierra-viva", 0.6)])
        result = search("Where is Tiera Viva?", live_data=True, vector_lookup=lookup)
        self.assertEqual(result.passage_ids, ["project:tierra-viva"])
        self.assertEqual([source.publisher for source in result.sources], ["Dar Global PLC"])
        self.assertIn("#page=", result.sources[0].url)

    def test_http_fallback_logs_a_sanitized_reason_and_cools_down(self):
        lookup = HybridLookup()
        question = "Which official paperwork proves the seller owns the home and the building was approved?"
        expected = search(question, live_data=True)
        with patch.dict(os.environ, CHAT_MODE="mock", PROPERTY_DATA_MODE="live", PROPERTY_RETRIEVAL_MODE="hybrid"):
            import main
        with (
            patch.object(main, "vector_lookup", lookup),
            patch.object(lookup, "_initialize", side_effect=TimeoutError("api-key: example-secret")) as initialize,
            self.assertLogs(HybridLookup.__module__, level="WARNING") as logs,
            TestClient(main.app) as client,
        ):
            self.assertEqual(client.get("/health").json(), {"status": "ok"})
            initialize.assert_not_called()
            for _ in range(2):
                response = client.post("/api/chat", json={"message": question})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["answer"], expected.message)
                self.assertEqual(response.json()["sources"], [])
        initialize.assert_called_once()
        self.assertEqual(len(logs.output), 1)
        self.assertIn("using JSONL retrieval", logs.output[0])
        self.assertIn("TimeoutError", logs.output[0])
        self.assertNotIn("example-secret", logs.output[0])
