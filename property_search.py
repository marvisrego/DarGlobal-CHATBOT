import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit

from answers import Source

SNAPSHOT = Path(__file__).resolve().parent / "snapshots" / "property_pilot.jsonl"
LIVE_SNAPSHOT = SNAPSHOT.with_name("property_live.jsonl")
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
            fields.append(f"Status: {record['status']}")
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
    return " · ".join(filter(None, fields))


def search(message, snapshot=None, *, live_data=False):
    snapshot = (LIVE_SNAPSHOT if live_data else SNAPSHOT) if snapshot is None else snapshot
    description = "browser-saved property" if live_data else "sample property"
    if not snapshot.is_file():
        return SearchResult(f"The {description} snapshot is unavailable.")
    with snapshot.open(encoding="utf-8") as infile:
        records = [json.loads(line) for line in infile if line.strip()]
    mode = "live" if live_data else "synthetic_fixture"
    selected = [record for record in records if record.get("mode") == mode and record.get("title")]
    if live_data:
        selected = [
            record for record in selected
            if record.get("collection_method") == "browser_saved_html" and valid_citation(record)
        ]
    projects = [record for record in selected if (record["source"], record["record_type"]) == ("DarGlobal", "project")]
    listings = [record for record in selected if (record["source"], record["record_type"]) == ("Wasalt", "listing")]
    query = normalized(message)
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
    intro = "Based on the saved property pages." if live_data else "Sample data from the property snapshot."
    return SearchResult(
        " ".join([intro, *notes]),
        [record_fact(record) for record in matches],
        [
            Source(
                title=record["title"], publisher=record["source"], fixture=not live_data,
                url=record["canonical_url"] if live_data else None,
            )
            for record in matches
        ],
    )


def valid_citation(record):
    parts = urlsplit(record.get("canonical_url") or "")
    expected_host = {"DarGlobal": "darglobal.co.uk", "Wasalt": "wasalt.sa"}.get(record.get("source"))
    return (
        expected_host is not None and parts.scheme == "https" and parts.netloc == expected_host
        and bool(parts.path.strip("/"))
    )
