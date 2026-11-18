# DarGlobal Property Research Assistant

**[Open the live demo on Render](https://darglobal-chatbot-4x3v.onrender.com/)**

Type a question and press **Enter**, or choose an example on the welcome screen. Keep a listing search and its refinements in the same conversation so the assistant can carry the filters forward. Use **New chat** to start an independent test. Listing cards and research answers include links to the original sources.

## Prompts to try

| What to test | Prompt | What to look for |
| --- | --- | --- |
| Greeting | Hi | A short welcome and an invitation to ask about properties. |
| Broad sale search | What properties can I buy? | A few real listings from the covered cities and an invitation to narrow the search. |
| Follow-up filters | After the broad search, ask "Only two bedrooms under SAR 900,000." | The assistant keeps the sale filter and finds a two-bedroom Al Marjan apartment in Riyadh with a published asking price of SAR 600,000. |
| Budget range | Find 3-bedroom apartments for sale in Riyadh between SAR 500,000 and SAR 600,000. | Results respect both ends of the range. |
| Project comparison | Compare Tierra Viva and Les Vagues across location and property type. | A cited comparison of the two DarGlobal developments. |
| Buyer guidance | What documents does Wasalt recommend checking before buying a home? | Wasalt guidance on the title deed, building permit, and completion certificate. |
| Missing price | What is the current asking price of AIDA? | The assistant explains that the available sources give no verified asking price and cites the project information. |
| Zero matches | Find 3-bedroom apartments for sale in Riyadh under SAR 10,000. | The assistant explains that the pilot has no match and offers to loosen the filters. |

## Data collected

| Data | Coverage in the demo | Where it came from |
| --- | --- | --- |
| DarGlobal projects | 14 project records with published locations, property types, dated statuses, and selected amenities or development values | The DarGlobal results presentation, public project and announcement pages, and the supporting AIDA announcement from OMRAN |
| Wasalt buyer guidance | 23 sections summarized from nine guides that cover buying checks, Ejar, off-plan risks, and neighborhoods in Riyadh, Jeddah, and Dammam | Visible text from public Wasalt guide pages |
| Wasalt sale listings | 14 Riyadh apartments with published asking prices, bedroom counts, and areas where available | Public individual listing pages reviewed using same-day indexed copies after direct access returned HTTP 403 |
| DarGlobal sale listings | Two units, one at Trump Tower Jeddah and one at AIDA in Muscat. Both have unpublished asking prices. | Public DarGlobal unit booking pages |

The collection process extracted selected public property facts and source links from publisher pages and PDFs. It left unpublished fields blank and excluded broker contact details. Imports used stable record IDs and verified stored rows by reading them back. [Data collection log](docs/data-collection-log.md), [Corpus v2](evaluation/corpus-v2.md), [Listing pilot](docs/listing-pilot.md)

**The inventory is a small snapshot of 16 sale listings.** Start with Riyadh for price and bedroom filters, or browse across the covered cities. Rental questions can use the Wasalt guides. The unit inventory covers sales only. Treat asking prices and project statuses as published observations, and check the source for current price or availability. Project development values apply to the development as a whole.

## How Docker and Render are used

Render builds the [Dockerfile](Dockerfile) from `main` using a Python 3.13 slim base image. The image packages the FastAPI app, browser interface, and validated project and guide snapshots. Build settings select the PostgreSQL and NVIDIA vector dependencies. Uvicorn serves the app on the assigned port, and Render uses `/health` for its health check. Credentials are supplied through the service environment.

The hosted app reads project, guide, and listing facts from Supabase Postgres. NVIDIA Nemotron Ultra chooses a read-only search tool when a question needs property data. Qdrant supplies semantic candidates from one shared collection with **53 vectors**. Postgres enforces the listing filters and supplies the canonical facts and links used for the answer. [Live health check](https://darglobal-chatbot-4x3v.onrender.com/health)

## Repository layout

| Folder | Contents |
| --- | --- |
| [app/](app/) | Chat and model integrations, data access, and retrieval |
| [static/](static/) | Browser interface |
| [scripts/](scripts/) | Data preparation, Qdrant maintenance, and demo checks |
| [tests/](tests/) and [evaluation/](evaluation/) | Automated tests, regression prompts, and source coverage |
