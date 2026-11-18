import unittest
from decimal import Decimal
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from pydantic import ValidationError

from listing_pilot import Listing, listing_intent, listing_result, parse_listing_query


def wasalt_fixture(source_id="9999991", **overrides):
    return {
        "source": "wasalt", "source_id": source_id,
        "source_url": f"https://wasalt.sa/en/property/sale/synthetic-pilot-fixture-{source_id}",
        "captured_at": "2025-02-03T12:00:00+03:00", "city": "Riyadh", "district": "Fixture District",
        "property_type": "Apartment", "transaction_type": "sale", "asking_price": "500000", "currency": "SAR",
        "bedrooms": 2, "area_sqm": "90.5", "features": ["Near a metro station"], **overrides,
    }


class ListingPilotTests(unittest.TestCase):
    def test_budget_forms_descriptive_preference_and_exact_city(self):
        query = parse_listing_query(
            "Find 2-bedroom Wasalt apartments for sale in Riyadh near a metro station within SAR 550,000.",
            ["riyadh", "riyadh heights"],
        )
        self.assertEqual((query.city, query.bedrooms, query.currency, query.budget, query.strict),
                         ("riyadh", 2, "SAR", Decimal("550000"), False))
        self.assertEqual(query.preference, "near a metro station")
        self.assertEqual(query.source, "wasalt")
        self.assertEqual(query.transaction_type, "sale")
        strict = parse_listing_query("Find homes in Riyadh Heights under SAR 500k.", ["riyadh", "riyadh heights"])
        self.assertEqual(strict.city, "riyadh heights")
        self.assertTrue(strict.strict)
        self.assertIsNone(strict.preference)
        with self.assertRaisesRegex(ValueError, "one city"):
            parse_listing_query("Find apartments in Jeddah below SAR 1 million.", ["riyadh"])

    def test_rent_period_must_be_explicit_for_budget(self):
        with self.assertRaisesRegex(ValueError, "monthly or yearly"):
            parse_listing_query("Show apartments for rent in Riyadh under SAR 5,000.", ["riyadh"])
        query = parse_listing_query("Show apartments for rent in Riyadh under SAR 5,000 per month.", ["riyadh"])
        self.assertEqual((query.transaction_type, query.rent_period), ("rent", "month"))

    def test_price_range_uses_both_inclusive_bounds(self):
        from listing_store import select_listings

        query = parse_listing_query(
            "Find 3-bedroom apartments in Riyadh between SAR 500,000 and SAR 600,000.", ["riyadh"],
        )
        self.assertEqual((query.minimum_price, query.budget, query.strict),
                         (Decimal("500000"), Decimal("600000"), False))
        bare = parse_listing_query("3-bedroom apartments in Riyadh SAR 500k–600k.", ["riyadh"])
        self.assertEqual((bare.minimum_price, bare.budget), (query.minimum_price, query.budget))
        connection = Mock()
        select_listings(connection, "v3", query)
        sql, params = connection.execute.call_args.args
        self.assertIn("asking_price <= %s", sql)
        self.assertIn("asking_price >= %s", sql)
        self.assertEqual(params[-2:], [Decimal("600000"), Decimal("500000")])
        with self.assertRaisesRegex(ValueError, "minimum budget"):
            parse_listing_query("Show apartments in Riyadh between SAR 600,000 and SAR 500,000.", ["riyadh"])
        with self.assertRaisesRegex(ValueError, "both budget limits"):
            parse_listing_query("Show apartments in Riyadh between SAR 600,000.", ["riyadh"])

    def test_descriptive_result_marks_preference_unverified(self):
        row = Listing.model_validate(wasalt_fixture())
        result = listing_result([row], "qdrant", has_preference=True)
        self.assertIn("requested preference remains unverified", result.message)

    def test_followup_is_scoped_to_previous_listing_ids(self):
        import main
        from listing_store import search_listings

        expensive = Listing.model_validate(wasalt_fixture("9999992", asking_price="600000"))
        cheap = Listing.model_validate(wasalt_fixture())
        connection = Mock()
        connection.execute.side_effect = [Mock(fetchone=Mock(return_value={"digest": "example"})),
                                          [cheap.model_dump(), expensive.model_dump()]]
        ids = [expensive.listing_id, cheap.listing_id]
        provider = Mock(side_effect=lambda message, **kwargs:
                        search_listings(connection, "v3", message, None, **kwargs))
        with TestClient(main.app) as client, patch.object(main, "listing_search", provider):
            reply = client.post("/api/chat", json={"message": "Which of these is the cheapest?", "listing_ids": ids})
        self.assertEqual(reply.status_code, 200)
        body = reply.json()
        self.assertEqual(body["listing_ids"], [cheap.listing_id])
        self.assertEqual([source["url"] for source in body["sources"]], [cheap.source_url])
        self.assertEqual(connection.execute.call_args.args[1], ("v3", ids))

    def test_validates_unit_citation_and_rejects_contact_data(self):
        for overrides in (
            {"source_url": "https://wasalt.sa/en/apartments-for-sale-in-riyadh"},
            {"source_url": "https://example.com/en/property/sale/synthetic-pilot-fixture-9999991"},
            {"source_url": "https://wasalt.sa/en/property/sale/synthetic-pilot-fixture-5555555"},
            {"transaction_type": "rent"},
            {"features": ["Call me on +966 123 456 789"]},
            {"phone": "private"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                Listing.model_validate(wasalt_fixture(**overrides))
        row = Listing.model_validate(wasalt_fixture(asking_price=None, area_sqm=None, district=None, bedrooms=None))
        self.assertIsNone(row.asking_price)
        self.assertIsNone(row.area_sqm)
        self.assertIsNone(row.district)
        self.assertIsNone(row.bedrooms)
        self.assertEqual(row.captured_at.isoformat(), "2025-02-03T09:00:00+00:00")
        reply = listing_result([row], "postgres")
        self.assertEqual(reply.sources[0].url, row.source_url)
        self.assertIn("gives no asking price", reply.facts[0].casefold())

    def test_routes_listing_prompts_and_preserves_corpus_questions(self):
        import main

        prompt = "Find two-bedroom Wasalt apartments in Riyadh up to SAR 550,000."
        self.assertTrue(listing_intent(prompt))
        self.assertFalse(listing_intent("What is the current asking price of AIDA?"))
        self.assertFalse(listing_intent("According to Wasalt's guide, what should I check before buying a home?"))
        listing = Listing.model_validate(wasalt_fixture())
        provider = Mock(return_value=listing_result([listing], "postgres"))
        with (
            TestClient(main.app) as client,
            patch.object(main, "listing_search", provider),
            patch.object(main, "LISTING_RELEASE", "v1"),
        ):
            response = client.post("/api/chat", json={"message": prompt})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["x-listing-release"], "v1")
            body = response.json()
            self.assertEqual(body["answer_path"], "source_summary")
            self.assertIn("SAR 500,000", body["answer"])
            self.assertIn("observed at capture", body["answer"])
            self.assertEqual(body["sources"][0]["url"], listing.source_url)
            provider.assert_called_once_with(prompt)
