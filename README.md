# DarGlobal-CHATBOT

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
reproducing site content, so the pilot recorded no live property data. A
request with `python -m ingest --live --output snapshots/property_live.jsonl`
reports zero real records and exits without writing a snapshot.

## Chat with the snapshot

Generate the fixture snapshot with `python -m ingest` before launching the app:

```powershell
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

Ask for a project by title or try “Find Wasalt apartments with 2 bedrooms
up to SAR 1 million.” The search applies exact bedroom and currency filters
and compares prices in the requested currency. “Under” is exclusive and
“up to” includes the stated price. All source cards are marked as samples.

To enable OpenRouter, set `$env:CHAT_MODE = "live"` before starting Uvicorn
and use the key in the local `.env`. OpenRouter ranks the matching sample
records. The server renders their facts and source cards from the snapshot.

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

## Docker and Render

The image includes the validated sample snapshot in
`deploy_data/property_pilot.jsonl` and uses the existing JSONL search and
filters. It has two fixtures and zero real records. A read-only snapshot is
enough for this pilot, so the service has no database or vector store.
The Docker command listens on `0.0.0.0` and uses `PORT`, defaulting to
`10000`. The health endpoint returns HTTP 200 with `{"status":"ok"}`
without calling OpenRouter or any other service.

Build and run a keyless local smoke test from PowerShell:

```powershell
docker build -t darglobal-chatbot .
docker run --rm --name darglobal-chatbot -e PORT=10000 -p 127.0.0.1:8004:10000 darglobal-chatbot
```

In another PowerShell terminal, check the health endpoint and a fixture query.
Use the same commands after deployment with the assigned Render HTTPS base URL.

```powershell
$base = "http://127.0.0.1:8004"
Invoke-RestMethod "$base/health"
$body = @{ message = "Find Wasalt apartments with 2 bedrooms up to SAR 1 million." } | ConvertTo-Json
Invoke-RestMethod "$base/api/chat" -Method Post -ContentType "application/json" -Body $body
```

The answer should include the Al Yasmin fixture at SAR 910,000 and a source
marked as a fixture with no URL.

To deploy, push the assignment commit to `origin/dev` and sign in to Render.
Connect this GitHub repository and deploy a **Free Docker Web Service** from
the `dev` branch using the root `Dockerfile`. The included `render.yaml`
also defines these settings for a Render Blueprint. Set the health check
path to `/health`. In the Render service's **Environment** settings, add
`OPENROUTER_API_KEY` using the real key, set `CHAT_MODE=live`, and set
`PROPERTY_DATA_MODE=fixture`. Keep the key out of the build and repository.
Once Render assigns an HTTPS URL, run the two smoke checks above against it.

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
