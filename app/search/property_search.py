import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from urllib.parse import urlsplit

from app.chat.answers import (
    DAR_REPORT_CITATION, DAR_REPORT_METHOD, DAR_REPORT_URL, DAR_WEB_METHOD,
    OMRAN_AIDA_METHOD, OMRAN_AIDA_URL, WASALT_GUIDE_METHOD, Evidence, ListingCard, Source,
)
from app.data.corpus_snapshot import ROOT, live_snapshot

SNAPSHOT = ROOT / "snapshots" / "property_pilot.jsonl"
LIVE_SNAPSHOT = live_snapshot("v1")
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
PROPERTY_TYPES = {
    "apartment", "villa", "mansion", "penthouse", "plot", "residence", "house", "townhouse", "studio",
}
PROJECT_NAME_WORDS = {"interiors", "tower", "development", "project", "residences"}
PROJECT_SCORE_FLOOR = 0.22
NVIDIA_PROJECT_SCORE_FLOOR = 0.15
GUIDE_SCORE_FLOOR = 0.28
SINGLE_PROJECT_LOCATION = re.compile(r"\b(?:where (?:is|was)|(?:what|which) (?:city|country))\b")
DEVELOPMENT_VALUE = re.compile(
    r"\b(?:gdv|(?:development|project) (?:value|valuation)|"
    r"(?:value|valuation) of (?:the |this )?(?:development|project))\b"
)
GEOGRAPHIC_GUIDE = re.compile(r"wasalt (.+) (?:district|neighbou?rhood) guide")
GUIDE_STOP_WORDS = {
    "about", "according", "area", "describe", "described", "describes", "district", "guide", "have",
    "highlight", "highlights", "into", "neighborhood", "neighbourhood", "that", "their", "there",
    "these", "this", "those", "wasalt", "what", "where", "which", "with", "would",
}
GUIDE_TOPICS = re.compile(
    r"\b(?:buy\w*|own\w*|sell\w*|rent\w*|lease\w*|tenan\w*|home\w*|hous\w*|propert\w*|"
    r"apartments?|condo\w*|build\w*|mainten\w*|repairs?|neighbou?r\w*|districts?|ejar|park|"
    r"commut\w*|off plan|developer\w*)\b"
)
TRANSIT_WORDING = re.compile(
    r"\b(?:trains?|metro|subway|public (?:transit|transport(?:ation)?)|don t drive|do not drive|without a car|no car)\b"
)
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
    abstain: bool = False
    retrieval_path: str = "jsonl"
    evidence: list[Evidence] = field(default_factory=list)
    required_notes: list[str] = field(default_factory=list)
    listing_cards: list[ListingCard] = field(default_factory=list)
    listing_context: object | None = None
    listing_followup: str | None = None


def normalized(text):
    return " ".join(re.findall(r"[^\W_]+", text.casefold()))


def includes(text, phrase):
    return f" {phrase} " in f" {text} "


def guide_alias_query(query, terms):
    if "riyadh" not in terms or not terms & {"commute", "commuting", "job", "office", "work"}:
        return query
    if not TRANSIT_WORDING.search(query) or terms & {"rent", "renting", "lease", "leasing"}:
        return query
    buying = terms & {"buy", "buying", "purchase", "purchasing"} or includes(query, "house hunting")
    choosing_home = terms & {"home", "house", "property", "neighborhood", "neighbourhood"} and terms & {
        "check", "choose", "consider", "look", "looking",
    }
    if buying or choosing_home:
        # Match the existing transit alias without changing project or listing filters.
        return f"{query} metro access"
    return query


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


def record_fields(record, *, include_development_value=True):
    fields = [record["title"], record.get("location")]
    if record["record_type"] == "project":
        if record.get("property_type"):
            fields.append(f"Property type: {record['property_type']}")
        if record.get("status"):
            as_of = f" (as of {record['source_as_of']})" if record.get("source_as_of") else ""
            fields.append(f"Status: {record['status']}{as_of}")
        if record.get("amenities"):
            fields.append(f"Amenities: {', '.join(record['amenities'])}")
        if include_development_value and record.get("development_value"):
            fields.append(record["development_value"])
        fields.extend(record.get("details", []))
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
    return list(filter(None, fields))


def record_fact(record, *, include_development_value=True):
    return "\n".join(record_fields(record, include_development_value=include_development_value))


def answer_evidence(record):
    is_guide = record["record_type"] == "buyer_guide"
    return Evidence(
        record["section"] if is_guide else record["title"], record.get("publisher") or record["source"],
        tuple(record["points"] if is_guide else record_fields(record)[1:] or [record["title"]]),
        is_guide,
    )


def property_types(terms):
    return {term.removesuffix("s") for term in terms} & PROPERTY_TYPES


def matches_project_filters(record, locations, types, complete, constructing):
    location = normalized(record.get("location") or "")
    recorded_types = property_types(normalized(record.get("property_type") or "").split())
    return (
        (not locations or any(includes(location, place) for place in locations))
        and (not types or bool(types & recorded_types))
        and (not complete or record.get("status") == "Completed")
        and (not constructing or record.get("status") == "Under construction")
    )


def mentions_source_date(query, record):
    if not record.get("source_as_of"):
        return False
    published = date.fromisoformat(record["source_as_of"])
    month = published.strftime("%B").casefold()
    return any(includes(query, spelling) for spelling in (
        normalized(published.isoformat()), f"{published.day} {month} {published.year}",
        f"{month} {published.day} {published.year}",
    ))


def guide_scope(query, guides, places):
    places = places | {
        match[1] for row in guides if (match := GEOGRAPHIC_GUIDE.fullmatch(normalized(row["title"])))
    }
    requested = {place for place in places if includes(query, place)}
    eligible = []
    local = []
    for row in guides:
        label = normalized(f"{row['title']} {urlsplit(row['canonical_url']).path}")
        guide_places = {place for place in places if includes(label, place)}
        if not requested or not guide_places or requested & guide_places:
            eligible.append(row)
        if requested & guide_places:
            local.append(row)
    return eligible, local


def descriptive_guide_matches(terms, local_guides):
    def keywords(words):
        return {word.removesuffix("s") for word in words if len(word) >= 4} - GUIDE_STOP_WORDS

    requested = keywords(terms)
    matches = []
    for row in local_guides:
        headings = keywords(normalized(f"{row['title']} {row['section']}").split())
        evidence = keywords(normalized(" ".join(row["points"])).split())
        score = len(requested & (evidence - headings))
        if score >= 2:
            matches.append((score, row["record_id"], row))
    best = max((score for score, _, _ in matches), default=0)
    return [match for match in matches if match[0] == best]


def one_typo(left, right):
    if len(left) > len(right):
        left, right = right, left
    if len(right) - len(left) > 1:
        return False
    first = next((index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]), len(left))
    if len(left) < len(right):
        return left[first:] == right[first + 1:]
    return first < len(left) and (
        left[first + 1:] == right[first + 1:]
        or (left[first:first + 2] == right[first:first + 2][::-1] and left[first + 2:] == right[first + 2:])
    )


def project_name_evidence(query, record):
    words = query.split()
    for phrase in (record["title"], *record.get("aliases", [])):
        alias = normalized(phrase).split()
        if not alias or len("".join(alias)) < 4 or not any(word.isalpha() for word in alias):
            continue
        for index in range(len(words) - len(alias) + 1):
            window = words[index:index + len(alias)]
            if not (set(window) - PROJECT_NAME_WORDS):
                continue
            if one_typo(" ".join(window), " ".join(alias)):
                return True
    return False


def rerank_passages(message, rows, rerank):
    if rerank is None or len(rows) < 2:
        return rows
    texts = [
        f"{row['title']}\n{row['section']}\n{' '.join(row['points'])}"
        if row["record_type"] == "buyer_guide" else record_fact(row)
        for row in rows
    ]
    return [rows[index] for index in rerank(message, texts)]


def recover_corpus_match(message, query, records, vector_lookup, *, guide_ids, project_ids, rerank=None):
    if not guide_ids and not project_ids:
        return None, "jsonl"
    semantic_query = message
    if guide_ids and any(includes(query, term) for term in ("rush hour", "peak traffic", "trial drive")):
        semantic_query += " test the commute to work before signing a lease"
    by_id = {row["record_id"]: row for row in records if row.get("record_id")}
    if rerank is None:
        hits = vector_lookup(semantic_query)
    else:
        hits = vector_lookup(semantic_query, eligible_ids=project_ids | guide_ids)
    if hits is None:
        return None, "jsonl_after_qdrant_failure"
    project_floor = PROJECT_SCORE_FLOOR if rerank is None else NVIDIA_PROJECT_SCORE_FLOOR
    projects = [by_id[key] for key, score in hits if key in project_ids and score >= project_floor]
    if projects:
        return rerank_passages(message, projects, rerank)[0], "qdrant"
    sections = [
        by_id[key] for key, score in hits
        if key in guide_ids and score >= GUIDE_SCORE_FLOOR
    ]
    if sections:
        return rerank_passages(message, sections, rerank)[0], "qdrant"
    return None, "jsonl"


def corpus_search(message, records, *, vector_lookup=None, rerank=None):
    query = normalized(message)
    projects = [row for row in records if row["record_type"] == "project"]
    terms = set(query.split())
    wants_development_value = bool(DEVELOPMENT_VALUE.search(query))
    named = [
        row for row in projects if any(
            includes(query, normalized(alias)) for alias in (row["title"], *row.get("aliases", []))
        )
    ]
    wants_wasalt = includes(query, "wasalt")
    project_intent = bool(named) or "darglobal" in terms or includes(query, "dar global") or (
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
    types = property_types(terms)
    matched_projects = named
    possible_names = [
        row for row in projects if project_name_evidence(query, row)
    ] if not named and (vector_lookup is not None or (project_intent and locations)) else []
    if not named and not possible_names and project_intent and locations and not SINGLE_PROJECT_LOCATION.search(query):
        matched_projects = [row for row in projects if matches_project_filters(row, locations, types, complete, constructing)]
        if wants_development_value:
            valued = [row for row in matched_projects if row.get("development_value")]
            matched_projects = [row for row in valued if mentions_source_date(query, row)] or valued
    guides = [row for row in records if row["record_type"] == "buyer_guide" and row.get("record_id")]
    cities = {normalized(row["location"].split(", ")[0]) for row in projects if row.get("location")}
    guides, local_guides = guide_scope(query, guides, cities)
    guide_candidates = []
    if not listing_intent and (wants_wasalt or not project_intent):
        alias_query = query if BUDGET.search(message) else guide_alias_query(query, terms)
        for row in guides:
            aliases = [normalized(alias) for alias in row.get("aliases", [])]
            hits = [alias for alias in aliases if includes(alias_query, alias)]
            if hits:
                score = sum(1 if alias == "off plan" else 1 + len(alias.split()) ** 2 for alias in hits)
                guide_candidates.append((score, row["record_id"], row))
        if not any(score > 1 for score, _, _ in guide_candidates) and (
            wants_wasalt or (not possible_names and GUIDE_TOPICS.search(query))
        ):
            guide_candidates.extend(descriptive_guide_matches(terms, local_guides))
    guide_candidates.sort(key=lambda match: (-match[0], match[1]))
    matched_listings = []
    notes = []
    retrieval_path = "jsonl"
    abstain = False
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
    specific_guide = bool(guide_candidates and guide_candidates[0][0] > 1)
    if vector_lookup is not None and not specific_guide and not listing_intent and (
        not matched_projects or wants_wasalt
    ) and not (
        BUDGET.search(message) or BEDROOMS.search(query)
        or terms & {"listings", "listing", "bedrooms", "bedroom", "bhk", "studio"}
    ):
        project_ids = {
            row["record_id"] for row in possible_names if row.get("record_id")
            and matches_project_filters(row, locations, types, complete, constructing)
        } if not matched_projects and not wants_wasalt else set()
        implicit_listing = types and not project_intent and bool(
            terms & {"find", "show", "list", "available", "availability"}
        )
        guide_intent = wants_wasalt or (
            not project_intent and not possible_names and not implicit_listing and bool(GUIDE_TOPICS.search(query))
        )
        recovered, retrieval_path = recover_corpus_match(
            message, query, records, vector_lookup,
            guide_ids={row["record_id"] for row in guides} if guide_intent else set(),
            project_ids=project_ids, rerank=rerank,
        )
        if recovered and recovered["record_type"] == "project":
            matched_projects = [recovered]
        elif recovered:
            guide_candidates = [(1, recovered["record_id"], recovered)]
    # The first-time buyer guide explicitly covers documents to check before purchase.
    document_check = terms & {"documents", "paperwork", "certificates", "deeds", "permits"}
    buying_home = terms & {"buy", "buying", "purchase", "purchasing"} and terms & {
        "home", "house", "property",
    }
    if not guide_candidates and wants_wasalt and not listing_intent and document_check and buying_home:
        guide_candidates = [
            (5, row["record_id"], row) for row in records if row.get("record_id") == "guide:buyer-checks"
        ]
    if project_intent and not matched_projects:
        notes.append("No matching DarGlobal project is in the real property snapshot.")
    if matched_projects:
        if terms & {"price", "pricing", "cost", "asking"}:
            if wants_development_value and any(row.get("development_value") for row in matched_projects):
                notes.append(
                    "Development value applies to the project as a whole. "
                    "The cited sources provide no verified asking price for a home."
                )
            else:
                notes.append("The snapshot has no verified asking price for these projects.")
                abstain = True
        if terms & {"availability", "available", "inventory"}:
            notes.append("The snapshot has no verified current unit availability.")
    required_notes = notes.copy()
    candidates = [*matched_projects, *matched_listings, *(match[2] for match in guide_candidates)]
    ranked = candidates[:5]
    if not ranked:
        return SearchResult(" ".join(notes) or "The snapshot has no evidence for that question.",
                            retrieval_path=retrieval_path)
    # Show specific matches and retain weaker candidates only in the retrieval trace.
    best = guide_candidates[0][0] if guide_candidates else 0
    shown = {key for score, key, _ in guide_candidates if score >= best / 2}
    eligible = [
        row for row in candidates if row["record_type"] != "buyer_guide" or row["record_id"] in shown
    ]
    if rerank is None:
        chosen = [row for row in ranked if row in eligible]
    else:
        # Reserve the structured matches before allowing any guide to take their place.
        protected = eligible[:min(len(matched_projects) + len(matched_listings), 5)]
        ordered = rerank_passages(message, eligible[:8], rerank)
        chosen = [row for row in ordered if row in protected]
        chosen += [row for row in ordered if row not in protected][:5 - len(protected)]
        ranked = (chosen + [row for row in candidates if row not in chosen])[:5]
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
            else record_fact(row, include_development_value=not abstain) for row in chosen
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
        abstain=abstain,
        retrieval_path=retrieval_path,
        evidence=[answer_evidence(row) for row in chosen],
        required_notes=required_notes,
    )


def search(message, snapshot=None, *, live_data=False, vector_lookup=None, rerank=None, record_loader=None):
    snapshot = (LIVE_SNAPSHOT if live_data else SNAPSHOT) if snapshot is None else snapshot
    description = "real property" if live_data else "sample property"
    if record_loader is None:
        if not snapshot.is_file():
            return SearchResult(f"The {description} snapshot is unavailable.")
        with snapshot.open(encoding="utf-8") as infile:
            records = [json.loads(line) for line in infile if line.strip()]
    else:
        records = record_loader()
    mode = "live" if live_data else "synthetic_fixture"
    selected = [record for record in records if record.get("mode") == mode and record.get("title")]
    if live_data:
        selected = [
            record for record in selected
            if valid_citation(record)
        ]
        if any(record.get("record_id") for record in selected):
            return corpus_search(message, selected, vector_lookup=vector_lookup, rerank=rerank)
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
        evidence=[answer_evidence(record) for record in matches],
        required_notes=notes,
    )


def valid_citation(record):
    if record.get("collection_method") == DAR_WEB_METHOD:
        url = urlsplit(record.get("canonical_url") or "")
        return (
            record.get("source") == "DarGlobal" and record.get("publisher") == "Dar Global PLC"
            and record.get("record_type") == "project" and record.get("source_url") == record.get("canonical_url")
            and url.scheme == "https" and url.netloc == "darglobal.co.uk" and not url.query and not url.fragment
            and (url.path.startswith("/press/") or url.path == "/amaya/padel-living")
            and bool(record.get("section"))
        )
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
