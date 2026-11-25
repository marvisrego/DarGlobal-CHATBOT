"""Find public DarGlobal and Wasalt sources when inventory cannot answer."""

from urllib.parse import urlsplit

import httpx

from app.chat.answers import Source
from app.search.property_search import SearchResult, includes, normalized


FIRECRAWL_SEARCH = "https://api.firecrawl.dev/v2/search"
DOMAINS = {
    "darglobal": ("DarGlobal", ("darglobal.co.uk", "www.darglobal.co.uk", "cdn.darglobal.co.uk")),
    "wasalt": ("Wasalt", ("wasalt.sa", "www.wasalt.sa", "blog.wasalt.sa", "wasalt.com", "www.wasalt.com")),
}


class FirecrawlSearch:
    def __init__(self, key):
        self.key = key

    async def __call__(self, client, question, *, publisher=None):
        words = normalized(question)
        if publisher is None:
            dar = includes(words, "darglobal") or includes(words, "dar global")
            wasalt = includes(words, "wasalt")
            if dar != wasalt:
                publisher = "darglobal" if dar else "wasalt"
        publishers = (DOMAINS[publisher],) if publisher else DOMAINS.values()
        allowed = {domain: label for label, names in publishers for domain in names}
        response = await client.post(
            FIRECRAWL_SEARCH, headers={"Authorization": f"Bearer {self.key}"},
            json={
                "query": question, "sources": [{"type": "web"}], "limit": 4,
                "includeDomains": list(allowed), "domainTools": False, "timeout": 8_000,
            },
            timeout=httpx.Timeout(10, connect=3),
        )
        response.raise_for_status()
        body = response.json()
        data = body["data"]
        if body["success"] is not True or not isinstance(data, dict):
            raise ValueError("Invalid web search result.")
        hits = data.get("web") or []
        if not isinstance(hits, list):
            raise ValueError("Invalid web search hits.")
        sources, excerpts, seen = [], [], set()
        for hit in hits:
            if not isinstance(hit, dict):
                continue
            url, title, description = (hit.get(field) for field in ("url", "title", "description"))
            if not isinstance(url, str) or not isinstance(title, str) or not title.strip():
                continue
            try:
                parts = urlsplit(url)
            except ValueError:
                continue
            if parts.scheme != "https" or parts.netloc.lower() not in allowed or url in seen:
                continue
            seen.add(url)
            label = allowed[parts.netloc.lower()]
            title = " ".join(title.split())[:180]
            sources.append(Source(title=title, publisher=label, url=url))
            if isinstance(description, str) and description.strip():
                excerpts.append(f"- {title}: {' '.join(description.split())[:350]}")
        if not sources:
            return None
        return SearchResult(
            "I found these public search results. Open the source pages to confirm the details and current availability.",
            facts=["\n".join(excerpts)] if excerpts else [], sources=sources, rankable=False, retrieval_path="web",
        )
