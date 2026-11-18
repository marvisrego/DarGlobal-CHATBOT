# NVIDIA hosted pipeline

The opt-in pipeline uses the direct NVIDIA APIs. Ultra selects claim IDs from
retrieved evidence, and the server validates the IDs before rendering the
answer. The API and chatbot identify the provider and model on success.
Failed generation displays the labeled source summary. Reasoning is disabled
in the request and never read into the answer.

| Stage | Model |
| --- | --- |
| Chat | `nvidia/nemotron-3-ultra-550b-a55b` |
| Embedding | `nvidia/nemotron-3.5-embed-1b`, 2,048 dimensions |
| Reranking | `nvidia/nemotron-3.5-rerank-8b` |

The dedicated `darglobal-wasalt-nvidia-embed-1b-v1` collection contains the
25 verified passages and reuses their stable point IDs and citation payloads.
Indexing uses `input_type=passage` and validates all returned vectors before
creating or writing the collection. Runtime lookup uses `input_type=query`
and validates the collection's dimension and embedding metadata.

Exact project matches and listing filters use the existing structured
retriever. NVIDIA reranks up to eight eligible passages and keeps up to five
for Ultra. Focused questions can use fewer passages when fewer pass eligibility.
Strong structured matches retain priority. When a guide question requires
vector recovery, the reranker can select the best eligible section. A reranking
timeout or invalid result preserves the original candidate order. Vector lookup
failure leaves the JSONL path available.

## Enable on Render

Set these values in the service's **Environment** settings. The collection name
is pinned in the application for this pipeline.

```dotenv
PROPERTY_DATA_MODE=live
CHAT_MODE=live
CHAT_PROVIDER=nvidia
PROPERTY_RETRIEVAL_MODE=nvidia
Nvidia_chat=<NVIDIA chat key>
Nvidia_Embed=<NVIDIA embedding key>
Nvidia_rerank=<NVIDIA reranking key>
QDRANT_URL=<Qdrant HTTPS endpoint>
QDRANT_API_KEY=<Qdrant key with read access to the NVIDIA collection>
```

Use **Save, rebuild, and deploy**. The Dockerfile uses
`PROPERTY_RETRIEVAL_MODE=nvidia` as its build argument to install the Qdrant
client. The model calls run against NVIDIA's hosted APIs. The Docker build
copies neither the environment file nor any API key. The default application
configuration remains JSONL retrieval and OpenRouter chat.

## Local verification

The indexer reads the exact `Nvidia_Embed` setting from ignored local
`.env` and uses the verified `snapshots/property_live.jsonl`. If the local
snapshot is absent, copy the tracked `deploy_data/property_live.jsonl`
into `snapshots/` first.

```powershell
python -m pip install -r requirements.txt -r requirements-vector-store.txt
python -m scripts.retrieval.index_qdrant --nvidia
python -m unittest discover -s tests -v
python -m scripts.checks.evaluate_corpus --nvidia
python -m scripts.checks.evaluate_corpus --nvidia --live
```

The live evaluation command runs two model checks. The full NVIDIA evaluation
runs the curated and recovery cases with mock generation so the scores isolate
retrieval and citation selection.

## Live acceptance and latency

Direct chat, embedding, and reranking smoke requests each returned HTTP 200.
The collection read-back confirmed 25 points in each of the NVIDIA and MiniLM
collections with identical IDs and citation payloads. The existing MiniLM
collection still uses 384 dimensions.

The following medians are from three local HTTP requests per question through
the FastAPI application. The comparison includes the upstream request, when
made. OpenRouter used source summaries during the baseline run, so this is
a comparison of the observed paths and excludes a successful OpenRouter
generation benchmark.

| Check | JSONL + OpenRouter | NVIDIA | Verified outcome |
| --- | ---: | ---: | --- |
| Amaya vs. Rayana | 0.664 s | 8.568 s | Ultra distinguished Amaya's development plots and Rayana's private mansions, citing both PDF pages. |
| Car-free Riyadh | 0.670 s | 1.386 s | Ultra selected the Wasalt claim about metro access for the work commute, with no additional transport advice. |
| Neptune and Wasalt costs | 0.605 s | 8.206 s | Ultra selected Neptune's villas and Wasalt's ongoing maintenance and service charges with separate citations. |
| AIDA asking price | 0.002 s | 0.003 s | Both modes abstained and kept the DarGlobal and OMRAN citations. |

The local browser smoke also passed with the NVIDIA badge and actual model
attribution. Hosted reranking sometimes timed out and retained the original
candidate order. Hosted embedding timeouts are retried once per query; a
continued failure falls back to JSONL for that request.
