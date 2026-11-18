# Real data collection log

Run date: 2026-11-18. Work branch: dev.

| Source | Method | Result | Next lead |
| --- | --- | --- | --- |
| [DarGlobal AIDA](https://darglobal.co.uk/landing-page/aida) | Normal Chrome through Playwright | The project rendered, including its project and amenities headings. The [DarGlobal terms](https://darglobal.co.uk/terms-conditions) require consent for copying or reproducing site material. No production record was made from this HTML. | Find an original announcement with different reuse terms. |
| [Wasalt Riyadh apartment results](https://wasalt.sa/en/apartments-for-sale-in-riyadh) | Normal Chrome through Playwright | The category rendered with individual listing links. The [general Wasalt terms](https://wasalt.sa/en/s/terms-of-use) restrict extraction and republication. | Check the listing-specific AI policy referenced by robots.txt. |
| [DarGlobal announcement](https://darglobal.co.uk/press/dar-al-arkan-and-omran-group-announce-partnership-to-develop-aida-overlooking-the-sea-of-oman) and [AIDA brochure](https://cdn.darglobal.co.uk/Aida_Phase_01_EN_8e90a16291.pdf) | Discovery through search and published policy review | Both are publisher originals. No separate grant covering the project site material was identified. | Check the joint venture partner's original announcement. |
| [DarGlobal press release on PR Newswire](https://www.prnewswire.com/news-releases/dar-global-awards-omr-9-3-million-infrastructure-contract-to-oman-shapoorji-company-llc-for-aida-phase-1--302180237.html) | Search and [host terms review](https://www.prnewswire.com/terms-of-use/) | The release is attributed to Dar Global. The host explicitly restricts scraping and use of site content for software development. | Use a first-party partner source if suitable. |
| [OMRAN AIDA project](https://omran.om/projects/aida) and [OMRAN partnership announcement](https://omran.om/index.php/news/2022/omran-group-and-dar-al-arkan-announce-partnership-to-develop-aida-in-yiti) | HTTPX, review of [robots.txt](https://omran.om/robots.txt) and [site terms](https://omran.om/index.php/privacy-policy-and-terms-of-use) | Both returned HTTP 200. The reviewed policy describes information handling and reserves rights to site content. The adapter records only normalized project facts from the original partner announcement. No article text or media is stored. | Verify the record and clickable partner citation in chat. |
| [Wasalt robots.txt](https://wasalt.sa/robots.txt) and [AI policy](https://wasalt.sa/llms.txt) | HTTPX for robots.txt, normal Chrome for the linked AI policy | The policy permits some extraction of public listing content with attribution. It says language models should not rely on cached or stored content, and restricts redistribution. | Look for a documented listing feed with local storage rights. |
| [Kite API Wasalt coverage](https://platform.kite.sa/sources) | [API documentation](https://platform.kite.sa/docs) and [license review](https://platform.kite.sa/legal/terms) | Kite documents Wasalt listings and permits limited caching and display for internal testing on its sandbox, subject to attribution, retention and access terms. Its listing API requires an account key. This workspace has no key. The API is a third-party route. | Use a publisher-approved export or authorized access with terms covering the assignment. |

The first run collected AIDA from OMRAN. Its location and mixed-use type
were checked against the partner announcement. That record keeps its
original publisher, citation and capture timestamp. Its current status
and amenities remain null.

## Publisher follow-up

| Publisher and source | Access, usage and verification | Captured UTC and method |
| --- | --- | --- |
| Dar Global PLC, [2026 half-year results presentation, page 24](https://cdn.darglobal.co.uk/Dar%20Global%20-%20HY%202026%20Results%20Presentation%20-%20Final.pdf#page=24) | The investor page links to this presentation. Its bytes match the issuer filing in the regulatory archive under accession NI-000152113. The [archive terms](https://data.fca.org.uk/artefacts/NSM_Terms_of_Use.pdf) license reuse of filed material but restrict deep links to the archive, so chat links to the issuer copy. Extracted and visually checked on page 24: Muscat, Oman; villas and apartments; under construction as of 2026-06-30. | 2026-11-18 10:35:01, HTTPX from the regulatory archive and PyMuPDF |
| Wasalt, [first-time buyer guide](https://blog.wasalt.sa/en/how-to-buy-a-house-in-saudi-arabia/) | Browser rendered the official blog linked by Wasalt. The [AI policy](https://wasalt.sa/llms.txt) limits extraction to public listing content but separately allows summaries of public articles and projects. Its freshness note addresses changing prices, availability, and listing status, and tells AI systems to consult the live site for real-time information. The [general terms](https://wasalt.sa/en/s/terms-of-use) restrict copying and republication. The local non-commercial demo keeps three short, attributed paraphrases of static advice, with no copied article text or listing data. The guide answer bypasses OpenRouter and is excluded from apartment filtering. | 2026-11-18 10:30:12, visible Chrome content and a local summary |

Current coverage is one distinct project (AIDA), supported by two
source records, and one Wasalt buyer guide with three checks. The real
snapshot contains zero Wasalt listings.

## Run the current snapshot locally

```powershell
$env:PROPERTY_DATA_MODE = "live"
$env:CHAT_MODE = "mock"
python -m uvicorn main:app --host 127.0.0.1 --port 8007
```

In a second PowerShell terminal:

```powershell
$base = "http://127.0.0.1:8007"
$question = @{ message = "Where is AIDA?" } | ConvertTo-Json
Invoke-RestMethod "$base/api/chat" -Method Post -ContentType "application/json" -Body $question
$question = @{ message = "What does Wasalt's buyer guide suggest checking before making an offer?" } | ConvertTo-Json
Invoke-RestMethod "$base/api/chat" -Method Post -ContentType "application/json" -Body $question
$question = @{ message = "Find Wasalt apartments in Riyadh up to SAR 1 million." } | ConvertTo-Json
Invoke-RestMethod "$base/api/chat" -Method Post -ContentType "application/json" -Body $question
```

AIDA cites the Dar Global report and OMRAN announcement when both are
in the snapshot. The Wasalt budget query returns no match and no source.
To import an approved browser-saved listing alongside AIDA, add
`--html FILE ORIGINAL_URL CAPTURED_AT` to the ingestion command. Use the
combined command to rebuild the snapshot with both inputs.

To add or refresh the issuer report and static guide, capture the visible
guide in Chrome using the Playwright CLI. Keep the capture under the ignored
`local_html/` directory and stop if the browser shows a challenge page.
Rebuild the OMRAN record separately only if the snapshot does not exist.

```powershell
python -m pip install PyMuPDF==1.27.2.3
if (-not (Test-Path snapshots/property_live.jsonl)) { python -m scripts.ingest --omran-aida }
$cli = (Resolve-Path node_modules/.bin/playwright-cli.cmd).Path
New-Item -ItemType Directory -Force local_html | Out-Null
Push-Location local_html
try {
    & $cli -s=wasalt-guide open "https://blog.wasalt.sa/en/how-to-buy-a-house-in-saudi-arabia/" --browser=chrome --headed
    & $cli -s=wasalt-guide snapshot --filename=guide.yml
    $captured = [DateTimeOffset]::UtcNow.ToString("o")
} finally {
    & $cli -s=wasalt-guide close
    Pop-Location
}
python -m scripts.ingest --dar-report --wasalt-guide local_html/guide.yml $captured
```

The last command preserves existing real records, including OMRAN, and
updates records with the same citation URL.
