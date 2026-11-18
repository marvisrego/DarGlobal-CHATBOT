# Unit listing pilot

## Source check on 18 November 2026

| Source | Public coverage observed | Units eligible for the hosted demo | Access needed |
| --- | --- | ---: | --- |
| DarGlobal | The project catalog lists developments. The production Trump Tower Jeddah booking page and linked navigator showed the same individual unit, with a reference, bedroom count and area. The reviewed unit detail had no asking price. | 0 | A developer-approved unit inventory export or feed with stable references and citations, plus permission to store, embed and display the data. Ask for unit-level asking prices if budget coverage is required. |
| Wasalt | The Riyadh sale results link to individual apartment pages. A detail page was opened to confirm that the links identify individual advertisements. Wasalt's terms restrict copying materials and republishing them outside Wasalt. | 0 | Written authorization covering the hosted demo and an approved Riyadh unit export or feed. |

- DarGlobal sources viewed. [Projects](https://darglobal.co.uk/projects), [the production booking page](https://darglobal.co.uk/product-category/trump-tower-jeddah), and [the linked residence navigator](https://navigator5.beyonity.com/?id=434849DF).
- Wasalt sources viewed. [Riyadh apartments for sale](https://wasalt.sa/en/apartments-for-sale-in-riyadh), [an individual listing page](https://wasalt.sa/en/property/sale/apartment-7128-sqm-with-2-bedrooms-5972147), and [Wasalt terms of use](https://wasalt.sa/en/s/terms-of-use).

## Publish an approved batch

Keep the approved export in an ignored folder such as `snapshots/`. Use a UTF-8 JSON object with an `approvals` map from `darglobal` or `wasalt` to the written approval or licence reference and a `listings` array. Review the approval scope for Supabase storage, NVIDIA embedding, the Qdrant index and display on the public demo.

| Listing field | Accepted value |
| --- | --- |
| `source`, `source_id` | Publisher key and stable unit or advertisement reference. The importer namespaces the stable listing ID by publisher. |
| `source_url`, `captured_at` | Production HTTPS detail or booking page identifying the referenced unit, and an ISO 8601 timestamp with offset. |
| `city`, `district`, `property_type` | Public names. Use singular property types such as `apartment` where possible. |
| `transaction_type`, `asking_price`, `currency`, `rent_period` | `sale` or `rent`, the exact observed asking amount, a three-letter currency code, and `month` or `year` if published for rent. |
| `bedrooms`, `area_sqm` | Published integer count and area in square metres. Use zero for an explicitly identified studio when the source supports that mapping. |
| `features`, `unit_key` | Optional, short published property or neighborhood feature labels. Supply a shared `unit_key` when listings across feeds identify the same physical unit. |

Leave unavailable facts null. Compare rows against the individual source links before publishing. Keep contact details, project valuations, starting-price ranges, and unverified attributes out of the input. Deduplicate by the publisher's stable ID, and resolve syndicated copies during source review before publishing.

```powershell
python -m publish_listings --input snapshots/approved-listings-v1.json --release v1 --validate-only
python -m publish_listings --input snapshots/approved-listings-v1.json --release v1
```

The importer verifies both stores and commits the Postgres release only after the vector index passes read-back. A retry of identical data is safe. Each release has a separate collection, such as `darglobal-wasalt-listings-nvidia-embed-1b-v1`. The v1 pilot accepts up to 10 units. Keep the existing project and guide corpus settings and opt into the completed listing release with `PROPERTY_LISTING_RELEASE=v1`. The runtime Postgres connection needs read access to both listing tables, including an RLS policy if it uses a dedicated non-owner role.

## Acceptance before expansion

1. Ask for apartments in a covered city, with a specific bedroom count and currency budget. Verify every returned unit's asking amount, bedrooms, city, district and URL against the detail record. Confirm that the reply dates each observation.
2. Repeat with a budget below all matching records. Confirm `answer_path=no_evidence` and zero listing citations.
3. Add a published preference, such as proximity to metro stations, to a city, bedroom and budget query. Confirm `retrieval_path=qdrant` and verify every returned unit directly. The Postgres filters apply before Qdrant ranking and remain in force even when vector lookup falls back.

After the real sample passes, use a new listing release with up to 100 distinct units total. Keep the pilot disabled until the release that will be served has passed the source and query checks.
