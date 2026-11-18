# Optional hybrid chatbot retrieval

The default is `PROPERTY_RETRIEVAL_MODE=jsonl`. Hybrid mode works with the
verified live corpus and queries the `darglobal-wasalt-minilm-v1` Qdrant
collection only when the structured retriever has uncertain or missing
evidence. A fixture session always uses its local snapshot.

Exact project names, aliases, and specific guide aliases take priority.
Listings still use the structured budget, currency, and bedroom filters.
Project location groups and recovered names use the recognized location,
property type, and reported status filters. A single-project location
question cannot expand into a list of unrelated projects in the same place.
Ranked project hits require a near match to a project name or alias in the
snapshot as well as a cosine score of at least 0.22. Guide recovery requires
a guide-related question and a cosine score of at least 0.28. Travel-timing
phrases such as “rush hour” expand to commute wording for the vector query.
These are retrieval heuristics based on a small corpus.

Indexing and search share the same local
`sentence-transformers/all-MiniLM-L6-v2` encoder at revision
`1110a243fdf4706b3f48f1d95db1a4f5529b4d41`. Both use the same tokenizer,
256-token limit, ONNX mean pooling and L2 normalization to produce
384-dimensional vectors. Qdrant receives the query vector and returns up
to five candidate IDs. The chatbot resolves those IDs against the verified
JSONL snapshot and uses its canonical text and citations.

The client checks the collection configuration before its first query. If
configuration, model loading, or the Qdrant request fails, the chat response
uses its JSONL evidence and the server logs the exception type. Retry starts
after a 30-second cooldown. The Qdrant client uses a two-second request
timeout. Health checks and ordinary exact matches avoid that dependency.

## Enable locally or on Render

Use these runtime settings in ignored local `.env` or as environment
variables on the service.

```dotenv
PROPERTY_DATA_MODE=live
PROPERTY_RETRIEVAL_MODE=hybrid
QDRANT_URL=<Qdrant HTTPS endpoint>
QDRANT_API_KEY=<Qdrant key with read access to the verified collection>
```

For live OpenRouter answers also set `CHAT_MODE=live` and
`OPENROUTER_API_KEY`. The chatbot pins the collection name. The indexer's
`QDRANT_COLLECTION` setting is independent.

For a local run, install the optional dependencies and cache the model.

```powershell
python -m pip install -r requirements-qdrant.txt
python -c "from property_vectors import Embedder; Embedder()"
python -m uvicorn main:app --host 127.0.0.1 --port 8008
```

For a future Render deployment, add the runtime settings above and select
**Save, rebuild, and deploy**. Render passes `PROPERTY_RETRIEVAL_MODE=hybrid`
to the Docker build, which installs the optional dependencies and caches
the public model. The build uses no Qdrant key. Set
`PROPERTY_RETRIEVAL_MODE=jsonl` to switch back. No Render settings were
changed during this evaluation, and the Docker build remains unverified
because Docker was unavailable locally. [Render Docker documentation](https://render.com/docs/docker)

## Evaluation

| Cases | Correct retrieval | Expected answer facts | Exact citation set |
| --- | ---: | ---: | ---: |
| [Original questions](questions.json) | 15/15 | 15/15 | 15/15 |
| [Prior paraphrases and misspellings](qdrant-queries.json) | 6/6 | 6/6 | 6/6 |
| [Fresh cases](hybrid-queries.json) | 14/14 | 14/14 | 14/14 |
| Live OpenRouter smoke tests | 2/2 | 2/2 | 2/2 |

Both Atlantis Berlin and the unsupported Riyadh apartment-budget query
returned zero matches and zero citations. Fresh cases also checked a
Neptune misspelling, service fees, developer history, mismatched location
and property type, and a broad off-plan alias whose intended section
described staged payments. Some of the fresh cases informed adjustments to
the retrieval rules. The default JSONL mode separately passed its original
15-case evaluation. A simulated Qdrant timeout through `/api/chat` verified
the JSONL fallback, the sanitized warning, and the retry cooldown.

- The live typo query “Where is Tiera Viva?” recovered the villas in
  Benahavís and cited Tierra Viva on PDF page 26. OpenRouter used
  `nvidia/nemotron-3.5-lightning:free`.
- The live paperwork paraphrase cited only Wasalt’s due-diligence section
  and returned the title deed, building permit, and completion certificate.
  OpenRouter used `cohere/north-mini-code:free`.

### Query embedding cost

Measured on a Windows Ryzen 7 6800H using one ONNX CPU thread. Cached
model initialization took **154 ms**, and the first query embedded in
**7.5 ms**. Each warm group used 60 single-query invocations.

| Query length | p50 | p95 |
| --- | ---: | ---: |
| Ordinary recovery questions | 4.4 ms | 6.3 ms |
| Near the API's 2,000-character limit | 65.9 ms | 70.1 ms |

These timings cover local encoding. Qdrant round trips, OpenRouter
inference, and Render performance are outside this measurement.

```powershell
New-Item -ItemType Directory -Force exports | Out-Null
python -m unittest discover -s tests -v
python -m scripts.evaluate_corpus --hybrid --output exports/hybrid-results.json
python -m scripts.evaluate_corpus --hybrid --openrouter --output exports/hybrid-openrouter-results.json
python -m scripts.evaluate_corpus --benchmark
```
