# Expanded corpus evaluation

See the [Qdrant evaluation](qdrant.md) for the vector search comparison on the
same 15 questions and six additional paraphrases and misspellings.

## Coverage

- 11 distinct DarGlobal projects in eight locations across six countries. Each
  project uses one record from the issuer's 2026 half-year filing, with its
  exact page, URL, capture time and status as of 2026-06-30. AIDA keeps its
  earlier OMRAN source as supporting evidence inside the same project record.
- Seven Wasalt public guides covering purchase checks, Ejar, expatriate
  rentals, Riyadh districts, off-plan buying, remote property management and
  King Salman Park. Two attributed sections per guide, with page URL, section
  title and capture time.
- 25 indexed passages: 11 project records and 14 guide sections. Zero real
  apartment listings. Real and synthetic snapshots remain separate.

The issuer pages are 16, 17, 19, 20, 22, 23, 24, 25, 26 and 27 of the
[publisher's presentation](https://cdn.darglobal.co.uk/Dar%20Global%20-%20HY%202026%20Results%20Presentation%20-%20Final.pdf).
The collection reads the byte-identical regulatory filing under the
[archive's reuse terms](https://data.fca.org.uk/artefacts/NSM_Terms_of_Use.pdf).
The Wasalt guide notes use the public-article summarization provision of
[Wasalt's AI policy](https://wasalt.sa/llms.txt) for the local non-commercial
demo. They exclude live listings, price estimates, legal rates and contact
details. The furnished rentals, Wasalt app and foreign-buyers candidate pages
returned 403 in Chrome and were excluded. No attempt was made to bypass them.

## What changed

The existing retrieval rules found the full expected evidence in 6 of 15
questions after admitting the expanded verified records for the comparison.
The recurring misses were shortened names, location and status queries,
cross-publisher questions, and section-level buyer guidance. The old guide
rule returned whichever section appeared first. See
[the baseline trace](baseline.json).

The new retrieval matches verified project aliases and uses location and
reported status for project group queries. It ranks guide sections by
specific query phrases and keeps weak secondary candidates in the trace
without citing them in the answer. Listing filters still operate on listing
records only. The model can order selected evidence but the server renders
the stored facts, missing-data messages, comparison summaries and citations.

## Results

| Check | Result | What passed |
| --- | --- | --- |
| Baseline retrieval | 6/15 | All expected passage IDs appeared in the first five, or no passage was returned for a negative case. |
| Updated retrieval | 15/15 | Same scoring rule. The [results](results.json) show each top-five trace. |
| Answer facts | 15/15 | The HTTP response contained the expected facts or acknowledged missing evidence. |
| Answer citations | 15/15 | The HTTP response's publisher, source title and URL matched the expected evidence set. |
| OpenRouter | 2/2 | DarGlobal Tierra Viva vs. Les Vagues with Nemotron; Wasalt staged payments vs. developer checks with Cohere North Mini Code. Both passed facts and citations. |

For the Wasalt run, OpenRouter received short, attributed paraphrases and the
request disallowed providers that collect prompt data. Two Gemma routes
returned HTTP 429 before the compatible Cohere route passed. See
[live run results](openrouter-results.json).

These are regression checks on a small curated set, with expected sources
and key facts in [questions.json](questions.json). They do not estimate
performance on unseen wording, multilingual queries, or a larger corpus.
The PDF is a historical status source and the guides do not establish
current listing inventory, prices or availability.

## Reproduce

Install optional collection dependencies and use ordinary Chrome. Raw
captures remain under ignored `local_html/`.

```powershell
python -m pip install -r requirements.txt
python -m pip install PyMuPDF==1.27.2.3 playwright==1.65.2
python -m capture_guides
python -m ingest --expand-corpus local_html/expanded_guides.json
python -m evaluate_corpus --output evaluation/results.json
python -m evaluate_corpus --openrouter --output evaluation/openrouter-results.json
```

If the Chrome captures are already present, skip `capture_guides` to use the
same capture times. The last command uses the existing server-side
`OPENROUTER_API_KEY` and calls two free models. The capture and evaluation
commands do not publish data or change the fixture snapshot.
