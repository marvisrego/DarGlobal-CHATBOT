import unittest
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from pydantic import ValidationError

from answers import Evidence
from conversational import PropertyAssistant
from corpus_snapshot import CorpusUnavailable, live_snapshot
from listing_pilot import ListingFilters, ListingToolArgs
from listing_store import resolve_filters
from property_search import SearchResult


def request(message):
    return SimpleNamespace(message=message, previous_question=None, listing_context=None, listing_ids=None)


def native_listing_call(arguments):
    return {"tool_calls": [{"id": "test-call", "type": "function",
                           "function": {"name": "search_listings", "arguments": arguments}}]}


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    def test_typed_filter_cannot_drop_explicit_budget_or_bedroom_count(self):
        prior = ListingFilters(transaction_type="sale")
        args = ListingToolArgs(refine_previous=True, bedrooms=3, maximum_price="1200000", currency="AED")
        filters, context = resolve_filters("Only two bedrooms under SAR 900,000", ["riyadh"], args, prior)
        self.assertEqual((filters.city, filters.bedrooms, filters.budget, filters.currency, filters.strict),
                         (None, 2, Decimal("900000"), "SAR", True))
        self.assertEqual(context.transaction_type, "sale")
        with self.assertRaises(ValidationError):
            ListingToolArgs.model_validate_json('{"sql":"SELECT 1"}')

    async def test_greeting_and_out_of_scope_do_not_retrieve(self):
        listing, records, vector = Mock(), Mock(), Mock()
        assistant = PropertyAssistant("unused", listing, records, vector, Mock())
        assistant.complete = AsyncMock(side_effect=[
            ("nvidia/test", {"content": '{"intent":"greeting"}'}),
            ("nvidia/test", {"content": '{"intent":"out_of_scope"}'}),
        ])
        for message in ("Hi", "Who won the game?"):
            reply = await assistant.run(request(message), "test")
            self.assertEqual(reply.result.retrieval_path, "none")
        listing.assert_not_called()
        records.assert_not_called()
        vector.assert_not_called()

    async def test_native_tool_result_is_returned_and_zero_results_offer_to_broaden(self):
        result = SearchResult("No verified unit listings in the pilot match those filters.", retrieval_path="postgres")
        listing = Mock(return_value=result)
        assistant = PropertyAssistant("unused", listing, Mock(), Mock(), Mock())
        assistant.complete = AsyncMock(side_effect=[
            ("nvidia/test", native_listing_call('{"transaction_type":"sale"}')),
            ("nvidia/test", {"content": '{"claims":[],"next":"broaden"}'}),
        ])
        reply = await assistant.run(request("What properties can I buy?"), "test")
        self.assertEqual(reply.path, "no_evidence")
        self.assertIn("adjust the budget", reply.answer)
        self.assertEqual(listing.call_args.kwargs["options"].transaction_type, "sale")
        tool_message = assistant.complete.call_args.args[1][-2]
        self.assertEqual((tool_message["role"], tool_message["tool_call_id"]), ("tool", "test-call"))

    async def test_backend_failure_is_separate_from_zero_results(self):
        listing = Mock(side_effect=CorpusUnavailable("listing_database_read_failed"))
        assistant = PropertyAssistant("unused", listing, Mock(), Mock(), Mock())
        assistant.complete = AsyncMock(return_value=("nvidia/test", native_listing_call("{}")))
        reply = await assistant.run(request("What properties can I buy?"), "test")
        self.assertEqual(reply.path, "tool_failure")
        self.assertIn("try again", reply.answer)

    async def test_invalid_claim_id_cannot_write_factual_answer(self):
        assistant = PropertyAssistant("unused", Mock(), Mock(), Mock(), Mock())
        assistant.complete = AsyncMock(return_value=(
            "nvidia/test", {"content": '{"claims":["p0c7"],"next":"none"}'},
        ))
        evidence = Evidence("Buyer guide", "Wasalt", ("Verify the title deed.",), is_guide=True)
        result = SearchResult("From the guide.", facts=["Verify the title deed."], evidence=[evidence])
        reply = await assistant.compose(None, [], result, "search_knowledge", "test")
        self.assertEqual(reply.path, "source_summary")
        self.assertEqual(reply.answer, "From the guide.\n\nVerify the title deed.")

    def test_knowledge_rewrite_can_disambiguate_vector_candidates(self):
        records = [json.loads(line) for line in live_snapshot("v2").read_text(encoding="utf-8").splitlines()]
        vector = Mock(return_value=[("guide:buyer-checks", 0.72), ("guide:offplan-risk", 0.71)])
        assistant = PropertyAssistant("unused", Mock(), lambda: records, vector, lambda _, passages: range(len(passages)))
        reply = assistant.search_knowledge(
            "How can I see if the builder has a good history before I buy a home that has yet to be built?",
            "Developer track record and off-plan risk", "test",
        )
        self.assertEqual(reply.passage_ids, ["guide:offplan-risk"])
        self.assertEqual(reply.retrieval_path, "qdrant")
        self.assertIn("track record", reply.facts[0])
        vector.assert_called_once()
