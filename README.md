# DarGlobal-CHATBOT

The reviewer demo covers **DarGlobal project information and Wasalt buyer guidance**.
The validated dataset contains 11 distinct projects and 14 sections from
seven guides. It contains no real apartment listings, current asking prices,
or verified unit availability. Project status is dated to the source.
The [15-question evaluation](evaluation/README.md) is a curated regression set
with separate retrieval, answer and citation checks.

## Property ingestion pilot

From the project root, install the Python requirements and run:

```powershell
python -m pip install -r requirements.txt
python -m ingest --limit-per-source 1 --output snapshots/property_pilot.jsonl
```

The command reads one synthetic discovery page per source and follows up to
one detail link per source by default (maximum five). Each run replaces the
local snapshot with UTF-8 JSONL. Records have a UTC collection timestamp and
`mode: synthetic_fixture`. The reserved `.invalid` URLs identify fictional
details. Missing fields are null and contact details are excluded.

| Source discovery page | Access review | Pilot mode |
| --- | --- | --- |
| [DarGlobal projects](https://darglobal.co.uk/projects) | [Robots.txt](https://darglobal.co.uk/robots.txt) permits the projects area. The [published terms](https://darglobal.co.uk/terms-conditions) restrict reuse. HTTPX received an Incapsula challenge instead of the project index. | Synthetic fixture |
| [Wasalt Riyadh apartments](https://wasalt.sa/en/apartments-for-sale-in-riyadh) | [Robots.txt](https://wasalt.sa/robots.txt) does not block the discovery path, but the [terms of use](https://wasalt.sa/en/s/terms-of-use) restrict copying and extraction. | Synthetic fixture |

The pilot runs offline. The adapters demonstrate parsing against the included
HTML and are unverified against publisher detail pages.

The direct [DarGlobal AIDA page](https://darglobal.co.uk/landing-page/aida)
opened in Chrome. DarGlobal's terms require written consent for copying or
reproducing site content, so the direct route produced no records. A
request with `python -m ingest --live --output snapshots/property_live.jsonl`
reports zero real records and exits without writing a snapshot.

## Chat with the snapshot

Generate the fixture snapshot with `python -m ingest` before launching the app:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

The default preview marks fixture sources as samples. For the reviewer demo,
set `PROPERTY_DATA_MODE=live` and `CHAT_MODE=live` in the launch environment.
OpenRouter ranks evidence with free Nemotron or Cohere models. The server
renders facts, source dates, missing-data messages and citations from the
snapshot. A fresh checkout can read the packaged snapshot directly.

## Import browser-saved pages

Use detail pages you have permission to reuse. Save each rendered page as
HTML under `local_html/` or outside the repository. Keep the original HTTPS
URL and the time you saved it, including the UTC offset. For Wasalt, save
each apartment detail page individually.

```powershell
python -m ingest --html local_html/aida.html "https://darglobal.co.uk/landing-page/aida" "<capture-time-with-offset>"
```

Repeat `--html FILE URL CAPTURED_AT` on the same command to import more pages.
The command reads files offline, rejects challenges and mismatched canonical
URLs, and reports counts by source. Valid records replace
`snapshots/property_live.jsonl` without changing the fixture snapshot. If
some inputs are rejected, the command writes the valid records and exits with
status 1. If all are rejected, it leaves any existing snapshot intact.

To query the imported snapshot with the existing UI:

```powershell
$env:PROPERTY_DATA_MODE = "live"
$env:CHAT_MODE = "mock"
python -m uvicorn main:app --host 127.0.0.1 --port 8004
```

This mode returns source links from imported records. It uses local lookup,
which can be verified independently of OpenRouter. Cross-check extracted
fields against each saved page before relying on the snapshot.

The [data collection log](docs/data-collection-log.md) documents the
alternatives checked and how to reproduce the OMRAN AIDA record.
See the [expanded corpus evaluation](evaluation/README.md) for the
current live snapshot, retrieval trace, and reproduction commands.

## Docker and Render

The image copies the validated real dataset from
`deploy_data/property_live.jsonl`. Refresh this file from a verified local
snapshot before rebuilding. A read-only JSONL snapshot serves the current
corpus and filters without a database or vector store.
The Docker command listens on `0.0.0.0` and uses `PORT`, defaulting to
`10000`. The health endpoint returns HTTP 200 with `{"status":"ok"}`
without calling OpenRouter or any other service.

Build and run the Docker demo from PowerShell. The key is read into the
current process environment and passed only at container runtime.

```powershell
docker build -t darglobal-chatbot .
$env:OPENROUTER_API_KEY = python -c "from dotenv import dotenv_values; print(dotenv_values('.env', interpolate=False)['OPENROUTER_API_KEY'])"
docker run --rm --name darglobal-chatbot -e OPENROUTER_API_KEY -e PORT=10000 -p 127.0.0.1:8004:10000 darglobal-chatbot
```

In another terminal, run the health and fresh Chrome smoke checks. Install
the optional browser dependency with `python -m pip install playwright==1.65.2`
if needed. Use the assigned Render HTTPS base URL to repeat these checks
after deployment.

```powershell
python -m smoke_demo http://127.0.0.1:8004
python -m smoke_demo https://your-service.onrender.com
```

The script verifies a project comparison and dated statuses, a Wasalt guide
answer, working publisher citations, and an explicit missing price response.

To deploy, push the assignment commit to `origin/dev` and sign in to Render.
Connect this GitHub repository and deploy a **Free Docker Web Service** from
the `dev` branch using the root `Dockerfile`. The included `render.yaml`
also defines these settings for a Render Blueprint. Set the health check
path to `/health`. In the Render service's **Environment** settings, add
`OPENROUTER_API_KEY` using the real key, set `CHAT_MODE=live`, and set
`PROPERTY_DATA_MODE=live`. Keep the key out of the build and repository.
Once Render assigns an HTTPS URL, run the browser smoke command against it.

## Excel export and future storage

From the project root, run `node export_corpus.mjs`. This uses the bundled
spreadsheet runtime in the Codex cache. If it is installed elsewhere, set
`CORPUS_ARTIFACT_NODE_MODULES` to its `node_modules` directory. The exporter
uses the local validated snapshot when present and the packaged copy in a
fresh checkout. It writes `exports/property_corpus.xlsx` and reopens it to
verify the data in all three sheets. Stable record and source IDs join the
project and guide rows to the Sources sheet, including the OMRAN supporting
source for AIDA. Export dependencies stay outside the runtime image.

`DATABASE_URL` is reserved for a future Supabase Postgres connection.
`QDRANT_URL`, `QDRANT_API_KEY`, and `QDRANT_COLLECTION` are reserved for
future vector search. All four settings can be empty for the current app.
Postgres migration, Qdrant provisioning and embeddings are deferred. Keep
future credentials in local or runtime settings.

## Keep-alive for the assignment review

In GitHub, open **Settings > Secrets and variables > Actions > New repository
secret**. Name it `RENDER_SERVICE_URL` and set its value to the Render base
HTTPS URL, for example `https://your-service.onrender.com`, without
`/health`.

Once the workflow is present on the default branch, go to **Actions >
Keep Render service alive > Run workflow**, select the branch, and click
**Run workflow**. Open the run and check that **Check Render health** passed.
GitHub requires the workflow on the default branch both for cron and to
enable manual dispatch. The copy on `dev` alone does not activate either.
After the review, open the same workflow under **Actions**, use its menu,
and select **Disable workflow**.

Render provides 750 free instance hours per calendar month, shared across
each workspace. Keeping one service running consumes those hours while it
is awake. The workflow is scheduled every 10 minutes. Delayed GitHub runs
can leave the Free service idle long enough for a cold start.
