import json
import re
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from answers import (
    DAR_REPORT_CITATION, DAR_REPORT_METHOD, DAR_REPORT_URL,
    OMRAN_AIDA_METHOD, OMRAN_AIDA_URL, WASALT_GUIDE_METHOD, Source,
)

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "property_pilot.jsonl"
LIVE_SNAPSHOT = SNAPSHOT.with_name("property_live.jsonl")
if not LIVE_SNAPSHOT.is_file():
    LIVE_SNAPSHOT = SNAPSHOT.parent.parent / "deploy_data" / "property_live.jsonl"
CURRENCIES = r"SAR|USD|AED|GBP|EUR|OMR|QAR|KWD|BHD|CAD|AUD|INR|JPY"
CAP = r"(\b(?:under|below|less than|up to|at most|no more than|max(?:imum)?(?: price)?)\b|<=|<)"
BUDGET = re.compile(
    rf"{CAP}\s*(?:of\s+)?[:=]?\s*(?:({CURRENCIES})\s*)?"
    rf"(\d+(?:,\d{{3}})*(?:\.\d+)?)(?:\s*(million|thousand|m|k)\b)?"
    rf"(?:\s*({CURRENCIES})\b)?(?!\d|[.,]\d)",
    re.IGNORECASE,
)
BEDROOMS = re.compile(r"\b(\d+)\s*[- ]?\s*(?:bedrooms?|beds?|br|bhk)\b")
NUMBER_WORDS = {"studio": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
NO_MATCH = "No sample records match that request."
HELP = (
    "Ask about a project by name, such as Sample Harbour Residences, or search Wasalt "
    "with filters like 2 bedrooms and up to SAR 1 million."
)


@dataclass
class SearchResult:
    message: str
    facts: list[str] = field(default_factory=list)
    sources: list[Source] = field(default_factory=list)
    rankable: bool = True
    passage_ids: list[str] = field(default_factory=list)
    supporting_sources: list[Source] = field(default_factory=list)
    has_guide: bool = False


def normalized(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def includes(text, phrase):
    return f" {phrase} " in f" {text} "


def listing_filters(message):
    query = message.casefold()
    bedroom_count = BEDROOMS.search(query)
    words = re.search(r"\b(one|two|three|four|five)[ -]+(?:bedrooms?|beds?|br|bhk)\b", query)
    bedrooms = int(bedroom_count[1]) if bedroom_count else (
        NUMBER_WORDS[words[1]] if words else (0 if re.search(r"\bstudio\b", query) else None)
    )
    codes = set(re.findall(rf"\b(?:{CURRENCIES})\b", message.upper()))
    if len(codes) > 1:
        raise ValueError("Choose one currency for the sample listing search.")
    currency = next(iter(codes), None)
    budget = BUDGET.search(message)
    if re.search(CAP, message, re.IGNORECASE) and not budget:
        raise ValueError("Enter a maximum price as a number, such as up to SAR 1 million.")
    if budget and not currency:
        raise ValueError("Include a currency with your maximum price, such as up to SAR 1 million.")
    amount = None
    strict = False
    if budget:
        cap, amount_text, scale = budget[1], budget[3], budget[4]
        multiplier = {"million": 1_000_000, "m": 1_000_000, "thousand": 1_000, "k": 1_000}
        amount = Decimal(amount_text.replace(",", "")) * multiplier.get((scale or "").lower(), 1)
        strict = cap.casefold() in {"under", "below", "less than", "<"}
    return bedrooms, currency, amount, strict


def matches_listing(record, filters):
    bedrooms, currency, amount, strict = filters
    if bedrooms is not None and record.get("bedrooms") != bedrooms:
        return False
    if currency and (record.get("currency") or "").upper() != currency:
        return False
    if amount is None:
        return True
    price = record.get("price")
    if price is None:
        return False
    price = Decimal(str(price))
    return price < amount if strict else price <= amount


def record_fact(record):
    fields = [record["title"], record.get("location")]
    if record["record_type"] == "project":
        if record.get("property_type"):
            fields.append(f"Property type: {record['property_type']}")
        if record.get("status"):
            as_of = f" (as of {record['source_as_of']})" if record.get("source_as_of") else ""
            fields.append(f"Status: {record['status']}{as_of}")
        if record.get("amenities"):
            fields.append(f"Amenities: {', '.join(record['amenities'])}")
    else:
        if record.get("price") is not None:
            price = f"{Decimal(str(record['price'])):,f}"
            if "." in price:
                price = price.rstrip("0").rstrip(".")
            fields.append(f"{record.get('currency') or 'Currency unspecified'} {price}")
        if record.get("bedrooms") is not None:
            fields.append(f"{record['bedrooms']} bedrooms")
        if record.get("area_sqm") is not None:
            fields.append(f"{record['area_sqm']:g} m²")
    return "\n".join(filter(None, fields))


def corpus_search(message, records):
    query = normalized(message)
    projects = [row for row in records if row["record_type"] == "project"]
    terms = set(query.split())
    named = [
        row for row in projects if any(
            includes(query, normalized(alias)) for alias in (row["title"], *row.get("aliases", []))
        )
    ]
    wants_wasalt = includes(query, "wasalt")
    project_intent = bool(named) or "darglobal" in terms or (
        not wants_wasalt and bool(terms & {"project", "projects", "development", "developments"})
    )
    listing_intent = wants_wasalt and (
        BUDGET.search(message) or BEDROOMS.search(message.casefold())
        or bool(terms & {"listings", "listing"})
        or (bool(terms & {"available", "availability", "apartment", "apartments"}) and
            not bool(terms & {"guide", "compare", "compound", "compounds"}))
    )
    locale = {
        normalized(part) for row in projects for part in (row.get("location") or "").split(", ") if part
    }
    locations = {place for place in locale if includes(query, place)}
    complete = bool(terms & {"completed", "complete", "finished", "delivered"})
    constructing = includes(query, "under construction")
    matched_projects = named
    if not named and project_intent and locations:
        matched_projects = [
            row for row in projects if any(includes(normalized(row.get("location") or ""), place) for place in locations)
            and (not complete or row.get("status") == "Completed")
            and (not constructing or row.get("status") == "Under construction")
        ]
    guide_candidates = []
    if not listing_intent:
        for row in records:
            if row["record_type"] != "buyer_guide" or not row.get("record_id"):
                continue
            aliases = [normalized(alias) for alias in row.get("aliases", [])]
            hits = [alias for alias in aliases if includes(query, alias)]
            if hits and (wants_wasalt or not project_intent):
                score = sum(1 if alias == "off plan" else 1 + len(alias.split()) ** 2 for alias in hits)
                guide_candidates.append((score, row["record_id"], row))
    guide_candidates.sort(key=lambda match: (-match[0], match[1]))
    matched_listings = []
    notes = []
    if listing_intent:
        try:
            filters = listing_filters(message)
        except ValueError as error:
            return SearchResult(str(error))
        matched_listings = [
            row for row in records if row["record_type"] == "listing" and matches_listing(row, filters)
            and (not includes(query, "riyadh") or includes(normalized(row.get("location") or ""), "riyadh"))
        ]
        if not matched_listings:
            notes.append("No Wasalt listings in the real property snapshot match those filters.")
    if project_intent and not matched_projects:
        notes.append("No matching DarGlobal project is in the real property snapshot.")
    if matched_projects:
        if terms & {"price", "pricing", "cost", "asking"}:
            notes.append("The snapshot has no verified asking price for these projects.")
        if terms & {"availability", "available", "inventory"}:
            notes.append("The snapshot has no verified current unit availability.")
    ranked = [*matched_projects, *matched_listings, *(match[2] for match in guide_candidates)][:5]
    if not ranked:
        return SearchResult(" ".join(notes) or "The snapshot has no evidence for that question.")
    # Show specific matches and retain weaker candidates only in the retrieval trace.
    best = guide_candidates[0][0] if guide_candidates else 0
    shown = {key for score, key, _ in guide_candidates if score >= best / 2}
    chosen = [
        row for row in ranked if row["record_type"] != "buyer_guide" or row["record_id"] in shown
    ]
    if len(matched_projects) > 1 and len({row.get("status") for row in matched_projects}) == 1:
        if all(row.get("status") and row.get("source_as_of") for row in matched_projects):
            date = matched_projects[0]["source_as_of"]
            if all(row["source_as_of"] == date for row in matched_projects):
                notes.append(f"These projects share the reported status: {matched_projects[0]['status']} (as of {date}).")
    if len(matched_projects) + len(matched_listings) > 5:
        notes.append("Showing five matching records.")
    guide_selected = any(row["record_type"] == "buyer_guide" for row in chosen)
    if guide_selected:
        notes.append("Source: Wasalt.sa for the guide notes.")
    return SearchResult(
        message=" ".join(["Based on the cited sources.", *notes]),
        facts=[
            f"{row['section']}: {' '.join(row['points'])}" if row["record_type"] == "buyer_guide"
            else record_fact(row) for row in chosen
        ],
        sources=[
            Source(title=row.get("section") or row["title"], publisher=row.get("publisher") or row["source"],
                   url=row["canonical_url"])
            for row in chosen
        ],
        passage_ids=[row.get("record_id", row["canonical_url"]) for row in ranked],
        supporting_sources=[
            Source(title=support["title"], publisher=support["publisher"], url=support["canonical_url"])
            for row in chosen for support in row.get("supporting_sources", []) if valid_citation(support)
        ],
        has_guide=guide_selected,
    )


def search(message, snapshot=None, *, live_data=False):
    snapshot = (LIVE_SNAPSHOT if live_data else SNAPSHOT) if snapshot is None else snapshot
    description = "real property" if live_data else "sample property"
    if not snapshot.is_file():
        return SearchResult(f"The {description} snapshot is unavailable.")
    with snapshot.open(encoding="utf-8") as infile:
        records = [json.loads(line) for line in infile if line.strip()]
    mode = "live" if live_data else "synthetic_fixture"
    selected = [record for record in records if record.get("mode") == mode and record.get("title")]
    if live_data:
        selected = [
            record for record in selected
            if valid_citation(record)
        ]
        if any(record.get("record_id") for record in selected):
            return corpus_search(message, selected)
    projects = [record for record in selected if (record["source"], record["record_type"]) == ("DarGlobal", "project")]
    listings = [record for record in selected if (record["source"], record["record_type"]) == ("Wasalt", "listing")]
    query = normalized(message)
    guide_query = includes(query, "wasalt") and any(
        includes(query, topic)
        for topic in ("buyer guide", "buying guide", "first time buyer", "due diligence", "before making an offer")
    )
    if live_data and guide_query and not BUDGET.search(message) and not BEDROOMS.search(message.casefold()):
        guide = next((record for record in selected if record["record_type"] == "buyer_guide"), None)
        if guide:
            return SearchResult(
                "Wasalt's guide suggests these checks before buying:",
                ["\n".join(f"- {point}" for point in guide["points"])],
                [Source(title=guide["title"], publisher="Wasalt", url=guide["canonical_url"])],
                rankable=False,
            )
        return SearchResult("The snapshot has no Wasalt buyer guide.")
    named = [
        record for record in projects
        if includes(query, normalized(record["title"]).removeprefix("sample "))
        or (live_data and includes(query, normalized(urlsplit(record["canonical_url"]).path.split("/")[-1])))
    ]
    wants_listings = bool(re.search(r"\b(?:wasalt|listings?|apartments?|bedrooms?|beds?|studio)\b", query))
    wants_project = bool(re.search(r"\b(?:darglobal|projects?|developments?|aida)\b", query)) or bool(named)
    if not wants_project and not wants_listings:
        return SearchResult("Ask for a DarGlobal project by name or a Wasalt listing by budget." if live_data else HELP)
    project_catalogue = bool(re.fullmatch(
        r"(?:(?:show|list|find)(?: me)? )?(?:all )?(?:sample )?(?:darglobal )?(?:sample )?(?:projects|developments)",
        query,
    ))
    matches = (named or (projects if project_catalogue else [])) if wants_project else []
    matches.sort(key=lambda record: record.get("collection_method") == OMRAN_AIDA_METHOD)
    notes = []
    if wants_project and not matches:
        notes.append(f"No matching DarGlobal project is in the {description} snapshot.")
    if wants_listings:
        try:
            filters = listing_filters(message)
        except ValueError as error:
            return SearchResult(str(error))
        found = [record for record in listings if matches_listing(record, filters)]
        if includes(query, "riyadh"):
            found = [record for record in found if includes(normalized(record.get("location") or ""), "riyadh")]
        if not found:
            notes.append(f"No Wasalt listings in the {description} snapshot match those filters.")
        matches += found
    matches = matches[:10]
    if not matches:
        return SearchResult(" ".join(notes) or ("No saved records match that request." if live_data else NO_MATCH))
    intro = "Based on the cited property sources." if live_data else "Sample data from the property snapshot."
    return SearchResult(
        " ".join([intro, *notes]),
        [record_fact(record) for record in matches],
        [
            Source(
                title=record["title"], publisher=record.get("publisher") or record["source"], fixture=not live_data,
                url=record["canonical_url"] if live_data else None,
            )
            for record in matches
        ],
    )


def valid_citation(record):
    if record.get("collection_method") == DAR_REPORT_METHOD:
        page = record.get("source_page")
        return (
            record.get("source") == "DarGlobal" and record.get("publisher") == "Dar Global PLC"
            and record.get("record_type") == "project" and record.get("source_url") == DAR_REPORT_URL
            and type(page) is int and 16 <= page <= 27
            and record.get("canonical_url") == f"{DAR_REPORT_CITATION.split('#')[0]}#page={page}"
        )
    if record.get("collection_method") == WASALT_GUIDE_METHOD:
        points = record.get("points")
        url = urlsplit(record.get("canonical_url") or "")
        return (
            record.get("source") == "Wasalt" and record.get("publisher") == "Wasalt"
            and record.get("record_type") == "buyer_guide"
            and record.get("source_url") == record.get("canonical_url")
            and url.scheme == "https" and url.netloc == "blog.wasalt.sa"
            and url.path.startswith("/en/") and isinstance(points, list)
            and bool(points) and all(isinstance(point, str) and point for point in points)
        )
    if record.get("collection_method") == OMRAN_AIDA_METHOD:
        return (
            record.get("source") == "DarGlobal" and record.get("publisher") == "OMRAN Group"
            and record.get("canonical_url") == OMRAN_AIDA_URL
        )
    if record.get("collection_method") != "browser_saved_html":
        return False
    parts = urlsplit(record.get("canonical_url") or "")
    expected_host = {"DarGlobal": "darglobal.co.uk", "Wasalt": "wasalt.sa"}.get(record.get("source"))
    return (
        expected_host is not None and parts.scheme == "https" and parts.netloc == expected_host
        and bool(parts.path.strip("/"))
    )
