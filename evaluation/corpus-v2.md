# Corpus v2

The release keeps all 25 records from v1 and appends 12 records verified from
the six publisher pages in the [expansion manifest](../data_sources/corpus_expansion.json).
It contains 37 passages, comprising 14 DarGlobal projects and 23 Wasalt guide
sections across nine articles. The three new DarGlobal records distinguish the
November 2026 Jeddah announcement, Trump Plaza Jeddah’s January 2026 launch
announcement, and Padel Living Residences. The dollar and riyal figures are
scoped to their project announcements. They are development values. Unit-price
requests retain the missing-price response. The Wasalt additions cover Jeddah
neighborhoods, six Dammam districts, and two Ejar workflow topics. The
snapshot contains no listings or fixture records.

## Reproduce the release

Use ordinary Chrome to capture the visible publisher text. The capture is kept
under ignored `local_html/`. The ingest step verifies the recorded dates,
headings, and evidence terms before appending the curated claims. Repeat
capture only to prepare a new release. A fresh timestamp changes the checksum
of the snapshot paired with the v2 cloud collection.

```powershell
python -m capture_guides --expansion
python -m ingest --append-corpus local_html/expansion-capture.json
Copy-Item deploy_data/property_live_v2.jsonl snapshots/property_live.jsonl
node export_corpus.mjs --preview
```

The workbook is written to `exports/property_corpus.xlsx` and independently
reopened to verify the saved cells. Its sources tab preserves both Ejar capture
times. The corresponding sections still belong to one article.

## Storage synchronization

Use the ignored local `.env`. Postgres imports use `DATABASE_URL` and the
existing `public.property_corpus` table. The importer can create the table
using the dedicated additive migration when it is absent. Each import
upserts by record ID in one transaction and verifies the stored fields before
commit. Qdrant uses `QDRANT_URL`, `QDRANT_API_KEY`, and `Nvidia_Embed`. The
versioned v2 collection uses the same NVIDIA 2,048-dimensional settings, stable
point IDs, and citation schema as v1. The v2 collection metadata includes the
snapshot checksum. When hosted embedding requests time out, rerun the index
command to resume the validated local checkpoint. The indexer requires all
passage vectors before creating or writing the collection.

```powershell
python -m pip install -r requirements.txt -r requirements-import.txt -r requirements-vector-store.txt
python -m import_corpus --input deploy_data/property_live_v2.jsonl
python -m import_corpus --input deploy_data/property_live_v2.jsonl
python -m index_qdrant --nvidia --corpus-version v2
```

Both Postgres imports and both Qdrant runs read back all 37 records. An
independent read-back compared all fields with the snapshot and confirmed the
same 37 canonical IDs in Postgres and Qdrant. It also verified the original
25-point NVIDIA collection against v1.

The default backend reads text and citations from JSONL. To serve the corpus
from Supabase Postgres, set `PROPERTY_CORPUS_BACKEND=postgres` and supply a
server-side Postgres connection string in `DATABASE_URL`. Qdrant still
supplies candidate IDs in NVIDIA retrieval mode. The shipped snapshot pins
the release membership and collection checksum. Postgres supplies the facts,
aliases, and citations on each request. The app returns HTTP 503 if the database
read fails or the release is incomplete.

## Switch the Render snapshot and collection together

The image contains both snapshots. With NVIDIA retrieval,
`PROPERTY_CORPUS_VERSION=v2` selects `property_live_v2.jsonl` and
`darglobal-wasalt-nvidia-embed-1b-v2` together. The app verifies the
collection’s snapshot checksum before vector lookup. Keep these settings in
the Render service’s Environment panel and rebuild the image.

```dotenv
PROPERTY_DATA_MODE=live
PROPERTY_CORPUS_VERSION=v2
PROPERTY_RETRIEVAL_MODE=nvidia
CHAT_MODE=live
CHAT_PROVIDER=nvidia
Nvidia_chat=<NVIDIA chat key>
Nvidia_Embed=<NVIDIA embedding key>
Nvidia_rerank=<NVIDIA reranking key>
QDRANT_URL=<Qdrant HTTPS endpoint>
QDRANT_API_KEY=<key with read access to the v2 collection>
```

For the Postgres-backed configuration, add the following Render settings.
Use a connection string with read access to `public.property_corpus`.
The API keys used by Supabase REST clients cannot read this private table.

```dotenv
PROPERTY_CORPUS_BACKEND=postgres
DATABASE_URL=<Supabase Postgres connection string>
```

Use **Save, rebuild, and deploy** so Docker installs the Qdrant dependency.
For the Postgres configuration, this also installs the Postgres client.
Check `/health` for `X-Corpus-Version: v2`, `X-Retrieval-Mode: nvidia`,
and `X-Corpus-Backend: postgres` when enabled. Setting `PROPERTY_CORPUS_VERSION=v1` switches
back to the original pair. `PROPERTY_RETRIEVAL_MODE=jsonl` uses the selected
snapshot on its own. [NVIDIA setup](nvidia.md)

```powershell
python -m unittest discover -s tests -v
python -m evaluate_corpus --hybrid
python -m evaluate_corpus --corpus-version v2
python -m evaluate_corpus --nvidia --corpus-version v2 --live
python -m evaluate_corpus --nvidia --corpus-version v2 --corpus-backend postgres --live
```

The local runs passed 14 unit tests, all 41 v1 MiniLM regression cases, and
all 25 v2 JSONL cases. The live v2 acceptance run passed four cases. Three
used NVIDIA Ultra with the expected citations, and the unit-price question
abstained. Browser checks confirmed the displayed text and links. A separate
v2 semantic query recovered the Al Badiyah guide passage from Qdrant and
returned its citation from v2 JSONL.
