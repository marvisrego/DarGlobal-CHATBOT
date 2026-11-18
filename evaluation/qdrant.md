# Qdrant corpus indexing and retrieval evaluation

The Qdrant Cloud collection `darglobal-wasalt-minilm-v1` has 25 verified points
from `snapshots/property_live.jsonl`. Each of the 11 DarGlobal projects and
14 sections across seven Wasalt guides maps to exactly one point. The fixture
snapshot is excluded. The chatbot continues to use its current JSONL retrieval.

Both passages and queries use
[`sentence-transformers/all-MiniLM-L6-v2`](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/tree/1110a243fdf4706b3f48f1d95db1a4f5529b4d41)
at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. The CPU ONNX
encoder produces 384-dimensional, L2-normalized vectors by mean-pooling
non-padding tokens. Qdrant uses cosine similarity. Model files are downloaded
on first use. Corpus text and query text are embedded locally.

Point IDs are UUIDv5 values derived from the existing Supabase `record_id`
values under a fixed repository namespace. Payloads contain the record ID,
type, title, searchable text, publisher, source URL, and citation fields.
The text includes aliases and stored facts for each passage. Citation fields
include the canonical URL, cited title, PDF page and status date where
applicable, capture timestamp and method, and supporting sources. The indexer
checks the collection's vector settings and embedding metadata before any
upsert. It also checks existing IDs to guard against conflicts with data from
another corpus.

The first cloud run created the collection and the second reused it. Both
runs verified the exact count and compared every stored ID and payload with
the input. In particular, AIDA's reported status date and OMRAN supporting
citation matched, as did Wasalt's buyer-check section title and article URL.
The original `DarglobalVector` collection uses a named 1,536-dimensional
vector and has no model metadata. The command refused to write to it.

## Retrieval results

The score follows the [existing evaluation](README.md). A query passes when
all expected records occur in the first five, or when retrieval returns zero
records for a question with an empty expected set. Qdrant used exact cosine
search without a score threshold. The table compares the
[original baseline](baseline.json), the current chatbot retriever on
`629feac`, and Qdrant Cloud. Embedded Qdrant gave the same rankings.

| Query set | Original baseline | Current retrieval | Qdrant |
| --- | ---: | ---: | ---: |
| [Curated questions](questions.json) | 6/15 | 15/15 | 13/15 |
| [New paraphrases and misspellings](qdrant-queries.json) | — | 0/6 | 6/6 |

| Curated query | Original baseline | Current retrieval | Qdrant ranks for expected records |
| --- | --- | --- | --- |
| AIDA location | Pass | Pass | 1 |
| Tierra Viva location and homes | Pass | Pass | 1 |
| Aston Martin beachfront project | Miss | Pass | 1 |
| Old Park Lane completion | Miss | Pass | 1 |
| Tierra Viva vs. Les Vagues | Miss | Pass | 1, 2 |
| Completed projects in Dubai | Miss | Pass | 3, 4 |
| Rayana, Wadi Safar | Pass | Pass | 1 |
| Buyer checks before an offer | Miss | Pass | 2 |
| Lease registration and installments | Miss | Pass | 1 |
| Testing the commute before leasing | Miss | Pass | 1 |
| Staged payments before completion | Miss | Pass | 1 |
| AIDA and off-plan risk | Miss | Pass | 4, 1, respectively |
| Wasalt apartments under SAR 1 million | Pass | Pass | Returned five candidates for a query with no expected records |
| AIDA current asking price | Pass | Pass | 1 for the project passage |
| DarGlobal Atlantis Berlin | Pass | Pass | Returned five candidates for an unknown project |

| New query | Qdrant rank for expected record |
| --- | ---: |
| Paperwork proving ownership and building approval | 1 |
| Trial drive during rush hour before renting in Riyadh | 4 |
| Repairs and cleaning while living overseas | 1 |
| Tiera Viva | 1 |
| Asteera | 1 |
| Les Vauges | 2 |

Vector search helped with alternate phrasing for buyer paperwork, commute
checks, and remote property management, and with all three misspellings.
Exact-name and alias matching give direct evidence for AIDA, Aston Martin,
and Old Park Lane. The current retriever puts both completed Dubai projects
and AIDA in the cross-publisher comparison ahead of unrelated passages.
It also rejects the unknown DarGlobal project name and applies listing
filters to the budget question. The AIDA price query retrieves the correct
historical project record, and the chatbot supplies the missing-price
response through its existing logic.

## Reproduce

From the repository root, use Python 3.13. If the local snapshot is absent in
a fresh checkout, restore the byte-identical verified copy.

```powershell
if (-not (Test-Path snapshots/property_live.jsonl)) {
    New-Item -ItemType Directory -Force snapshots | Out-Null
    Copy-Item deploy_data/property_live.jsonl snapshots/property_live.jsonl
}
python -m pip install -r requirements-qdrant.txt
python -m scripts.retrieval.index_qdrant --dry-run
```

For cloud runs, set `QDRANT_URL` and `QDRANT_API_KEY` in ignored local
`.env`. The default target comes from `QDRANT_COLLECTION`. Use the
explicit override below for the verified cloud collection.

```powershell
python -m scripts.retrieval.index_qdrant --collection darglobal-wasalt-minilm-v1
python -m scripts.retrieval.index_qdrant --collection darglobal-wasalt-minilm-v1
New-Item -ItemType Directory -Force exports | Out-Null
python -m scripts.retrieval.index_qdrant --collection darglobal-wasalt-minilm-v1 --evaluate --output exports/qdrant-results.json
```

The trace includes all five IDs and cosine scores for each Qdrant query,
plus the corresponding current and original retrieval results where
available. To run without Qdrant credentials, use the embedded store under
ignored `exports/qdrant-local`.

```powershell
python -m scripts.retrieval.index_qdrant --local
python -m scripts.retrieval.index_qdrant --local
python -m scripts.retrieval.index_qdrant --local --evaluate --output exports/qdrant-local-results.json
```
