# DarGlobal Property Research Assistant

**[Open the live demo on Render](https://darglobal-chatbot-4x3v.onrender.com/)**

Type a question and press **Enter**, or choose an example on the welcome screen. Keep a listing search and its refinements in the same conversation so the assistant can carry the filters forward. Use **New chat** to start an independent test. Listing cards and research answers include links to the original sources.

## Prompts to try

| What to test | Prompt | What to look for |
| --- | --- | --- |
| Greeting | Hi | A short welcome and an invitation to ask about properties. |
| Broad sale search | What properties can I buy? | A few real listings from the covered cities and an invitation to narrow the search. |
| City, bedrooms, and budget | Find four-bedroom apartments for sale in Dammam under SAR 650,000. | Wasalt listings match the city and bedroom count. Every published asking price is below the cap. |
| Compare displayed listings | After the Dammam search, ask "Which of these is the cheapest?" | The assistant compares the asking prices of the listings it just showed. |
| Strict budget cap | Find two-bedroom apartments for sale in Jeddah under SAR 500,000. | Each result is a two-bedroom apartment in Jeddah with a published asking price below SAR 500,000. |
| Budget range | Show four-bedroom apartments for sale in Madinah between SAR 450,000 and SAR 650,000. | Each listing respects both ends of the price range. |
| Neighborhood preference | Show apartments for sale in Jubail under SAR 1,000,000. I prefer to be near the waterfront. | The city and budget stay in force. Check the listing source to confirm waterfront proximity. |
| Project comparison | Compare Tierra Viva and Les Vagues across location and property type. | A cited comparison of the two DarGlobal developments. |
| Buyer guidance | What documents does Wasalt recommend checking before buying a home? | Wasalt guidance on the title deed, building permit, and completion certificate. |
| Missing price | What is the current asking price of AIDA? | The assistant explains that the available sources give no verified asking price and cites the project information. |
| Zero matches | Find three-bedroom apartments for sale in Riyadh under SAR 10,000. | The assistant reports that the verified inventory has no match and offers to adjust the filters. |

## Data collected

The assignment named DarGlobal and Wasalt and left the choice of data types open. I chose data that would let the chatbot help a prospective buyer compare developments, find individual homes within a budget, and understand what to check before buying. DarGlobal's project material describes developments, Wasalt's buyer guides cover the purchase process, and public unit listings from both publishers support searches by location, price, and bedroom count where those details are published.

| Data | Coverage in the demo | Where it came from |
| --- | --- | --- |
| DarGlobal projects | 14 project records with published locations, property types, dated statuses, and selected amenities or development values | The DarGlobal results presentation, public project and announcement pages, and the supporting AIDA announcement from OMRAN |
| Wasalt buyer guidance | 23 sections summarized from nine guides that cover buying checks, Ejar, off-plan risks, and neighborhoods in Riyadh, Jeddah, and Dammam | Visible text from public Wasalt guide pages |
| Wasalt sale listings | 164 apartments across Saudi cities including Riyadh, Dammam, Jeddah, Jubail, Makkah, and Madinah. The records include published asking prices, bedroom counts, and areas where available. | Public individual listing pages reviewed using same-day indexed copies after direct access returned HTTP 403 |
| DarGlobal sale listings | Two units, one at Trump Tower Jeddah and one at AIDA in Muscat. Both have unpublished asking prices. | Public DarGlobal unit booking pages |

The collection process extracted selected public property facts and source links from publisher pages and PDFs. It left unpublished fields blank and excluded broker contact details. Imports used stable record IDs and verified stored rows by reading them back. [Data collection log](docs/data-collection-log.md), [Corpus v2](evaluation/corpus-v2.md), [Initial listing pilot](docs/listing-pilot.md)

**The November 25, 2026 snapshot contains 166 sale listings.** The Wasalt guides also cover rental topics, including Ejar. Check the linked sources for current prices and availability. Project development values apply to the development as a whole.

## Architecture

### From question to answer

1. The browser sends questions to FastAPI at `POST /api/chat`. It saves chat history locally and sends previous listing filters with follow-up questions, so a request such as “only two bedrooms” can narrow the existing search.
2. NVIDIA Nemotron Ultra chooses a read-only tool. `search_listings` applies structured filters such as city, transaction type, bedroom count, and price range in Postgres. `search_knowledge` searches project descriptions and buyer guides using structured matches and semantic candidates from Qdrant. Greetings can skip retrieval.
3. The backend resolves candidates against the stored source records. Nemotron uses the retrieved evidence to compose the reply, and FastAPI builds listing cards and citations from the source records. The app distinguishes an empty search, an unpublished price, and a retrieval failure.

### Databases and models

| Component | Role in the hosted demo |
| --- | --- |
| **Supabase Postgres** | Stores project and guide records in `property_corpus` (corpus `v2`), unit records in `property_listing`, and release metadata in `property_listing_release` (active release `v3`). Supplies source URLs and applies exact listing filters. |
| **Qdrant** | Holds 203 vectors in one collection covering 37 project and guide records and 166 listings. Semantic search returns candidate record IDs for the backend to resolve against Postgres. |
| **NVIDIA Nemotron Ultra** (`nvidia/nemotron-3-ultra-550b-a55b`) | Routes questions to tools and composes evidence-backed replies. The hosted demo calls NVIDIA's API directly as the inference provider. |
| **NVIDIA Nemotron Embed** (`nvidia/nemotron-3.5-embed-1b`) | Creates 2,048-dimensional vectors for indexing and embeds search queries at runtime. |
| **NVIDIA Nemotron Rerank** (`nvidia/nemotron-3.5-rerank-8b`) | Reorders eligible project and guide candidates by relevance. If reranking is unavailable, the search keeps the existing candidate order. |

### Docker and Render

Render builds the [Dockerfile](Dockerfile) from `main` using Python 3.13 slim. The image packages FastAPI, the browser interface, and the validated project and guide snapshots. Build settings include the dependencies for Postgres and NVIDIA retrieval. Uvicorn serves the app on Render's assigned port, and credentials are supplied through the service environment. [Live health check](https://darglobal-chatbot-4x3v.onrender.com/health)

## Repository layout

| Folder | Contents |
| --- | --- |
| [app/](app/) | Chat and model integrations, data access, and retrieval |
| [static/](static/) | Browser interface |
| [scripts/](scripts/) | Data preparation, Qdrant maintenance, and demo checks |
| [tests/](tests/) and [evaluation/](evaluation/) | Automated tests, regression prompts, and source coverage |
