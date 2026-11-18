import unittest
from unittest.mock import Mock, patch

import httpx

from app.chat.answers import Evidence
from app.data.corpus_snapshot import live_snapshot
from app.chat.nvidia_api import DIMENSION, EMBED_MODEL, NvidiaRetrievalAPI
from app.chat.openrouter_provider import ModelUnavailable, generated_answer
from app.search.property_search import corpus_search, search


class NvidiaPipelineTests(unittest.TestCase):
    def test_embedding_modes_validate_dimension(self):
        api = NvidiaRetrievalAPI("unused")
        self.addCleanup(api.close)
        valid = {"model": EMBED_MODEL, "data": [{"index": 0, "embedding": [1.0] * DIMENSION}]}
        invalid = {"model": EMBED_MODEL, "data": [{"index": 0, "embedding": [1.0]}]}
        with patch.object(api, "_post", side_effect=[valid, valid, invalid]) as post:
            self.assertEqual(len(api.encode(["one"], input_type="passage")[0]), DIMENSION)
            api.encode(["two"])
            with self.assertRaises(ValueError):
                api.encode(["three"])
        self.assertEqual([call.args[2]["input_type"] for call in post.call_args_list], ["passage", "query", "query"])

    def test_rerank_timeout_preserves_candidate_order(self):
        api = NvidiaRetrievalAPI("unused", "unused")
        self.addCleanup(api.close)
        with (
            patch.object(api, "_post", side_effect=httpx.ReadTimeout("upstream detail")),
            self.assertLogs("uvicorn.error.darglobal", level="WARNING") as logs,
        ):
            self.assertEqual(api.rerank("query", ["one", "two"]), [0, 1])
        self.assertNotIn("upstream detail", logs.output[0])

    def test_rerank_uses_eight_eligible_rows_and_preserves_exact_project(self):
        records = [{
            "record_id": "project:test", "record_type": "project", "title": "Example Tower",
            "canonical_url": "https://example.com/project", "publisher": "DarGlobal",
        }]
        records.extend({
            "record_id": f"guide:example-{i}", "record_type": "buyer_guide", "title": f"Guide {i}",
            "section": f"Section {i}", "points": ["A guide fact."], "aliases": ["guide facts"],
            "canonical_url": f"https://example.com/guide/{i}", "publisher": "Wasalt",
        } for i in range(9))
        rerank = Mock(side_effect=lambda message, texts: list(reversed(range(len(texts)))))
        result = corpus_search("Example Tower and Wasalt guide facts", records, rerank=rerank)
        self.assertEqual(len(rerank.call_args.args[1]), 8)
        self.assertEqual(len(result.evidence), 5)
        self.assertIn("project:test", result.passage_ids)

    def test_structured_price_constraints_bypass_reranking(self):
        rerank = Mock()
        result = search("Find Wasalt apartments in Riyadh under SAR 1 million.", live_data=True, rerank=rerank)
        self.assertFalse(result.sources)
        self.assertIn("No Wasalt listings", result.message)
        rerank.assert_not_called()

    def test_hosted_typo_recovery_filters_before_vector_ranking(self):
        lookup = Mock(return_value=[("project:les-vagues", 0.1724)])
        rerank = Mock()
        result = search("Where is Les Vauges?", live_data=True, vector_lookup=lookup, rerank=rerank)
        lookup.assert_called_once_with("Where is Les Vauges?", eligible_ids={"project:les-vagues"})
        self.assertEqual(result.passage_ids, ["project:les-vagues"])
        self.assertEqual(result.retrieval_path, "qdrant")
        rerank.assert_not_called()

    def test_hosted_guide_recovery_filters_other_cities_before_ranking(self):
        lookup = Mock(return_value=[("guide:districts-transit", 0.9), ("guide:jeddah-neighborhoods", 0.4)])
        rerank = Mock()
        result = search(
            "Which Jeddah districts does Wasalt recommend for cyclists?",
            snapshot=live_snapshot("v2"), live_data=True, vector_lookup=lookup, rerank=rerank,
        )
        eligible = lookup.call_args.kwargs["eligible_ids"]
        self.assertIn("guide:jeddah-neighborhoods", eligible)
        self.assertIn("guide:buyer-checks", eligible)
        self.assertNotIn("guide:districts-transit", eligible)
        self.assertNotIn("guide:dammam-faisaliah", eligible)
        self.assertEqual(result.passage_ids, ["guide:jeddah-neighborhoods"])
        self.assertEqual(result.retrieval_path, "qdrant")
        rerank.assert_not_called()

    def test_only_validated_final_content_is_rendered(self):
        evidence = [Evidence("Guide", "Publisher", ("A supported recommendation.",), is_guide=True)]
        reply = {"model": "nvidia/model", "choices": [{"finish_reason": "stop", "message": {
            "content": '["p0c0"]', "reasoning_content": "This should never be returned.",
        }}]}
        generated = generated_answer(httpx.Response(200, json=reply), evidence, provider="nvidia")
        self.assertEqual(generated.text, "A supported recommendation.")
        self.assertEqual(generated.provider, "nvidia")
        reply["choices"][0]["message"]["content"] = '<think>Reasoning</think>["p0c0"]'
        with self.assertRaises(ModelUnavailable):
            generated_answer(httpx.Response(200, json=reply), evidence, provider="nvidia")
