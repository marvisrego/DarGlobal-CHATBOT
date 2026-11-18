# Unit listing pilot

## Source check on 18 November 2026

| Source | Public coverage observed | Units in the first release | Coverage limit |
| --- | --- | ---: | --- |
| DarGlobal | The Trump Tower Jeddah booking page and linked navigator showed the same individual unit with a reference, bedroom count, and area. The page omitted the asking price. | 1 | Several reviewed booking pages showed no units. A priced inventory feed would improve budget coverage. |
| Wasalt | The Riyadh sale results link to individual apartment pages. Direct requests returned HTTP 403. The first release uses same-day indexed copies of the individual pages with matching price, bedroom, and location fields. | 9 | The direct access block and unavailable or stale indexed pagination limit how many entries can be verified. A current export or feed would improve coverage. |

- DarGlobal sources viewed. [Projects](https://darglobal.co.uk/projects), [the production booking page](https://darglobal.co.uk/product-category/trump-tower-jeddah), and [the linked residence navigator](https://navigator5.beyonity.com/?id=434849DF).
- Wasalt sources viewed. [Riyadh apartments for sale](https://wasalt.sa/en/apartments-for-sale-in-riyadh), [an individual listing page](https://wasalt.sa/en/property/sale/apartment-7128-sqm-with-2-bedrooms-5972147), and [Wasalt terms of use](https://wasalt.sa/en/s/terms-of-use).

Both sources were used on the basis of the confirmation supplied for this pilot following a conversation with a DarGlobal employee.

## Published listing releases

| Listing release | Wasalt | DarGlobal | Total |
| --- | ---: | ---: | ---: |
| `v1` | 9 priced Riyadh apartments | 1 Jeddah unit | 10 |
| `v3` | 14 priced Riyadh apartments | 1 Jeddah unit and 1 Muscat unit | 16 |

Listing release `v3` supersedes `v2` and corrects the Muscat unit's capture timestamp. The Muscat unit was verified against the [AIDA production booking page](https://darglobal.co.uk/product-category/invest-in-aida). Both DarGlobal units have null asking prices. These units appear in searches without a budget filter. The listing collections and tables are separate from the 37-record project and guide corpus.

## Publish a verified batch

Keep the export in an ignored folder such as `snapshots/`. Use a UTF-8 JSON object with an `approvals` map from `darglobal` or `wasalt` to the recorded reuse basis and a `listings` array.

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
python -m scripts.data.publish_listings --input snapshots/approved-listings-v1.json --release v1 --validate-only
python -m scripts.data.publish_listings --input snapshots/approved-listings-v1.json --release v1
```

The importer verifies the vector index before opening the Postgres publication transaction, so a slow embedding request leaves queries for an existing release available. A retry of identical data is safe. The v1 pilot accepts up to 10 units. The runtime Postgres connection needs read access to both listing tables, including an RLS policy if it uses a dedicated non-owner role.

## Shared Qdrant collection for Render

The shared collection serves project, guide, and listing retrieval. Keep the existing live v2 corpus, Postgres, and NVIDIA settings. Set the following variable in Render.

```dotenv
QDRANT_COLLECTION=darglobal-wasalt-nvidia-corpus-v2-listings-v3
```

The collection name selects listing release `v3` automatically. It takes precedence over the older `PROPERTY_LISTING_RELEASE` setting. Project and guide candidate IDs still come from the configured corpus. Listing candidate IDs still come from the filtered Postgres query.

The following command copies the existing NVIDIA vectors into a shared collection for a published pair.

```powershell
python -m scripts.retrieval.combine_qdrant --corpus-version v2 --listing-release v3
```

The command checks both source releases, then verifies the shared collection's IDs, payloads, and vectors by read-back. The already published source collections support rollback and rebuilding.

## Acceptance before expansion

1. Ask for apartments in a covered city, with a specific bedroom count and currency budget. Verify every returned unit's asking amount, bedrooms, city, district and URL against the detail record.
2. Repeat with a budget below all matching records. Confirm `answer_path=no_evidence` and zero listing citations.
3. Add a published preference, such as proximity to metro stations, to a city, bedroom and budget query. Confirm `retrieval_path=qdrant` and verify every returned unit directly. The Postgres filters apply before Qdrant ranking and remain in force even when vector lookup falls back.

After the real sample passes, use a new listing release with up to 100 distinct units total. Keep the pilot disabled until the release that will be served has passed the source and query checks.
