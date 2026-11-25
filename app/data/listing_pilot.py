"""Approved unit listing schema, exact filters, and source-backed rendering."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import re
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from app.chat.answers import ListingCard, Source
from app.search.property_search import (
    BUDGET, BEDROOMS, CURRENCIES, SearchResult, correct_property_spelling, includes, listing_filters, normalized,
    one_typo, property_types,
)


PUBLISHERS = {"darglobal": "Dar Global PLC", "wasalt": "Wasalt"}
LISTING_RESULT_LIMIT = 10
RELEASE = re.compile(r"v[1-9][0-9]{0,5}\Z")
LISTING_FIELDS = (
    "source", "source_id", "source_url", "captured_at", "city", "district", "property_type", "transaction_type",
    "asking_price", "currency", "rent_period", "bedrooms", "area_sqm", "features", "unit_key",
)
LISTING_NOUN = re.compile(r"\b(?:listings?|apartments?|villas?|townhouses?|penthouses?|homes?|units?|studios?)\b")
SEARCH_TERMS = re.compile(r"\b(?:find|show|search|list|available|for sale|for rent|to buy|to rent|looking for)\b")
LISTING_LOCATION = re.compile(
    r"\bin\s+([\w'-]+(?:\s+[\w'-]+){0,4}?)(?=\s+(?:under|below|up to|at most|less than|between|"
    r"with|near|for sale|for rent|and|within|from|that|costing|please)\b|[?.!,]|$)", re.I,
)
PREFERENCE = re.compile(r"\b(?:near|close to|walking distance|walkable|waterfront|quiet|family friendly|with|prefer)\b")
BUDGET_ALIAS = re.compile(
    r"\b(?:(?:with(?:in)? (?:a )?)?budget of|at or below|within(?=\s+(?:[A-Z]{3}\s*)?\d))\b", re.I,
)
RANGE_AMOUNT = (
    rf"(?:(?:{CURRENCIES})\s*)?\d+(?:,\d{{3}})*(?:\.\d+)?"
    rf"(?:\s*(?:million|thousand|m|k)\b)?(?:\s*(?:{CURRENCIES})\b)?"
)
PRICE_RANGE = re.compile(
    rf"\b(?:between|from)\s+({RANGE_AMOUNT})\s*(?:and|to|[-–—])\s*({RANGE_AMOUNT})(?!\d|[.,]\d)", re.I,
)
BARE_PRICE_RANGE = re.compile(rf"(?<![\d.,])(\b{RANGE_AMOUNT})\s*(?:\bto\b|[-–—])\s*({RANGE_AMOUNT})(?!\d|[.,]\d)", re.I)
RANGE_CURRENCY = re.compile(rf"\b(?:{CURRENCIES})\b|(?:{CURRENCIES})(?=\d)", re.I)
PRICE_RANGE_START = re.compile(rf"\b(?:between|from)\s+(?=(?:{CURRENCIES})\s*\d|\d)", re.I)
CHEAPEST_FOLLOWUP = re.compile(
    r"(?:which(?: one)?(?: of (?:these|those|them)(?: (?:listings|units|options|apartments))?)?|what)"
    r" is (?:the )?(?:cheapest|lowest priced)"
    r"(?: (?:one|listing|option|unit))?"
)
CONTACT = re.compile(r"@|https?://|\b(?:whatsapp|e-?mail|call me|contact agent)\b|(?:\d[ .()+-]*){7,}", re.I)
SourceKey = Literal["darglobal", "wasalt"]
Label = Annotated[str, StringConstraints(min_length=1, max_length=100)]
Key = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")]
Amount = Annotated[Decimal, Field(gt=0, max_digits=20, decimal_places=4)]
BudgetAmount = Annotated[Decimal, Field(ge=0, max_digits=20, decimal_places=4)]
PropertyType = Literal["apartment", "villa", "mansion", "penthouse", "plot", "residence", "house", "townhouse", "studio"]


class ListingFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    city: Label | None = Field(default=None, description="City name. Omit for a search across all cities.")
    publisher: SourceKey | None = None
    transaction_type: Literal["sale", "rent"] | None = None
    property_types: Annotated[list[PropertyType], Field(max_length=5)] | None = None
    bedrooms: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    minimum_price: BudgetAmount | None = None
    maximum_price: BudgetAmount | None = None
    strict_maximum: bool | None = Field(default=None, description="True for under/below, false for up to.")
    rent_period: Literal["month", "year"] | None = None
    preference: Annotated[str, StringConstraints(min_length=1, max_length=250)] | None = None


class ListingToolArgs(ListingFilters):
    refine_previous: bool = Field(default=False, description="Apply these changes to the last successful listing search.")
    cheapest_previous: bool = Field(default=False, description="Compare the prices of the last displayed listings.")


def collection_name(release):
    if not RELEASE.fullmatch(release):
        raise ValueError("Listing release must be v1 or a higher version number.")
    return f"darglobal-wasalt-listings-nvidia-embed-1b-{release}"


class Listing(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    source: SourceKey
    source_id: Key
    source_url: Annotated[str, Field(max_length=600)]
    captured_at: AwareDatetime
    city: Label | None = None
    district: Label | None = None
    property_type: Label | None = None
    transaction_type: Literal["sale", "rent"] | None = None
    asking_price: Amount | None = None
    currency: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    rent_period: Literal["month", "year"] | None = None
    bedrooms: Annotated[int, Field(strict=True, ge=0, le=100)] | None = None
    area_sqm: Amount | None = None
    features: Annotated[list[Label], Field(max_length=16)] = Field(default_factory=list)
    unit_key: Key | None = None

    @field_validator("captured_at")
    @classmethod
    def utc_capture(cls, value):
        if value > datetime.now(UTC):
            raise ValueError("Capture time must not be in the future.")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def verify_fields(self):
        url = urlsplit(self.source_url)
        if url.scheme != "https" or not url.hostname or url.netloc != url.hostname or url.query or url.fragment:
            raise ValueError("Use a canonical HTTPS citation URL without query parameters or fragments.")
        if self.source == "wasalt":
            match = re.fullmatch(r"/(?:en/)?property/(sale|rent)/[^/]+-([0-9]+)/?", url.path)
            if url.hostname != "wasalt.sa" or not match or match[2] != self.source_id:
                raise ValueError("Wasalt citations must be individual property pages ending in the source listing ID.")
            if self.transaction_type and self.transaction_type != match[1]:
                raise ValueError("Transaction type conflicts with the Wasalt detail URL.")
        elif url.hostname not in {"darglobal.co.uk", "www.darglobal.co.uk"} or url.path in {"", "/", "/projects", "/projects/"}:
            raise ValueError("Use a production DarGlobal page that identifies the source unit.")
        if self.rent_period and self.transaction_type != "rent":
            raise ValueError("Rent period applies to rental units only.")
        if any(CONTACT.search(text) or not normalized(text)
               for text in [self.city or "", self.district or "", self.property_type or "", *self.features] if text):
            raise ValueError("Use location, property type and feature labels without contact details.")
        return self

    @property
    def listing_id(self):
        return f"{self.source}:{self.source_id.casefold()}"

    @property
    def city_key(self):
        return normalized(self.city) if self.city else None

    @property
    def title(self):
        return f"{(self.property_type or 'Unit').capitalize()} · {self.source_id}"


@dataclass(frozen=True)
class ListingQuery:
    city: str | None
    bedrooms: int | None = None
    currency: str | None = None
    budget: Decimal | None = None
    minimum_price: Decimal | None = None
    strict: bool = False
    source: SourceKey | None = None
    transaction_type: str | None = None
    types: tuple[str, ...] = ()
    rent_period: str | None = None
    preference: str | None = None


def budget_range(message):
    return PRICE_RANGE.search(message) or next(
        (match for match in BARE_PRICE_RANGE.finditer(message) if RANGE_CURRENCY.search(match[0])), None,
    )


def listing_intent(message):
    message = correct_property_spelling(message)
    query = normalized(message)
    if not LISTING_NOUN.search(query) or includes(query, "guide"):
        return False
    return bool(SEARCH_TERMS.search(query) or BUDGET.search(message) or BUDGET_ALIAS.search(message)
                or PRICE_RANGE_START.search(message) or budget_range(message) or BEDROOMS.search(query)
                or re.search(r"\blistings?\b", query))


def covered_city(value, cities):
    city = normalized(value)
    if city in cities:
        return city
    if len(city) < 5:
        return None
    matches = [candidate for candidate in cities if len(candidate) >= 5 and one_typo(city, candidate)]
    return matches[0] if len(matches) == 1 else None


def parse_listing_query(message, cities, *, require_city=True):
    message = correct_property_spelling(message)
    query = normalized(message)
    matches = sorted((city for city in cities if includes(query, city)), key=len, reverse=True)
    matches = [city for index, city in enumerate(matches) if not any(includes(other, city) for other in matches[:index])]
    if not matches:
        location = LISTING_LOCATION.search(message)
        if location:
            city = covered_city(location[1], cities)
            if city:
                matches.append(city)
        else:
            words = query.split()
            lengths = {len(city.split()) for city in cities}
            matches = list({
                city for length in lengths for start in range(len(words) - length + 1)
                if (city := covered_city(" ".join(words[start:start + length]), cities))
            })
    if len(matches) > 1 or (require_city and not matches):
        raise ValueError("Include one city covered by the listing pilot.")
    terms = set(query.split())
    price_query = BUDGET_ALIAS.sub("up to", message)
    bedrooms, currency, budget, strict = listing_filters(price_query)
    minimum_price = None
    price_range = budget_range(price_query)
    description = price_query
    if price_range:
        if not currency or budget is not None:
            raise ValueError("Use one budget range with a currency, such as between SAR 500,000 and SAR 600,000.")
        _, _, minimum_price, _ = listing_filters(f"up to {price_range[1]} {currency}")
        _, _, budget, _ = listing_filters(f"up to {price_range[2]} {currency}")
        if minimum_price > budget:
            raise ValueError("The minimum budget must be at or below the maximum.")
        description = price_query[:price_range.start()] + " " + price_query[price_range.end():]
    elif PRICE_RANGE_START.search(price_query):
        raise ValueError("Give both budget limits, such as between SAR 500,000 and SAR 600,000.")
    rent = any(includes(query, phrase) for phrase in ("for rent", "to rent", "rentals", "rental"))
    sale = any(includes(query, phrase) for phrase in ("for sale", "to buy"))
    monthly = bool(terms & {"monthly", "month"})
    yearly = bool(terms & {"yearly", "annual", "annually", "year"})
    if rent and budget is not None and monthly == yearly:
        raise ValueError("Specify a monthly or yearly budget for rentals.")
    source = None
    if "wasalt" in terms:
        source = "wasalt"
    if "darglobal" in terms or includes(query, "dar global"):
        source = None if source else "darglobal"
    # Budget and bedroom phrases can contain "with" without expressing a preference.
    description = BUDGET.sub(" ", description)
    description = re.sub(r"\bwith\s+(?:\d+|one|two|three|four|five)\s*[- ]?\s*bedrooms?\b", " ", description, flags=re.I)
    description = re.sub(r"\bwith\s+(?:a\s+)?\s*(?=[,.!?]|$|near\b)", " ", description, flags=re.I)
    description = normalized(description)
    preference = PREFERENCE.search(description)
    return ListingQuery(
        city=matches[0] if matches else None, bedrooms=bedrooms, currency=currency, budget=budget, minimum_price=minimum_price,
        strict=strict, source=source,
        transaction_type="rent" if rent else "sale" if sale else None, types=tuple(sorted(property_types(terms))),
        rent_period=("month" if monthly else "year") if rent and monthly != yearly else None,
        preference=description[preference.start():] if preference else None,
    )


def listing_result(rows, retrieval_path, *, has_preference=False):
    if not rows:
        return SearchResult("No verified unit listings in the pilot match those filters.", retrieval_path=retrieval_path)
    facts = []
    sources = []
    for row in rows:
        fields = [row.title, ", ".join(filter(None, (row.district, row.city)))]
        if row.transaction_type:
            fields.append(f"For {row.transaction_type}")
        if row.asking_price is not None:
            currency = f"{row.currency} " if row.currency else ""
            amount = format(row.asking_price.normalize(), ",f")
            period = f" per {row.rent_period}" if row.rent_period else ""
            unknowns = []
            if not row.currency:
                unknowns.append("currency unpublished")
            if row.transaction_type == "rent" and not row.rent_period:
                unknowns.append("rent period unpublished")
            context = f" ({', '.join(unknowns)})" if unknowns else ""
            fields.append(f"Asking price reported by the source: {currency}{amount}{period}{context}")
        else:
            fields.append("The listing source gives no asking price.")
        if row.bedrooms is not None:
            fields.append(f"{row.bedrooms} bedrooms")
        if row.area_sqm is not None:
            fields.append(f"{row.area_sqm.normalize():f} m²")
        if row.features:
            fields.append(f"Published features: {', '.join(row.features)}")
        facts.append("\n".join(filter(None, fields)))
        sources.append(Source(title=row.title, publisher=PUBLISHERS[row.source], url=row.source_url))
    message = "Unit listings matching your filters."
    if has_preference:
        message = ("Unit listings matching your location and other filters. "
                   "The requested preference remains unverified. Check the published features below.")
    if retrieval_path == "postgres_after_qdrant_failure":
        message += " Preference ranking was temporarily unavailable."
    cards = [ListingCard(title=source.title, lines=fact.splitlines()[1:], source=source)
             for fact, source in zip(facts, sources, strict=True)]
    return SearchResult(message, facts=facts, sources=sources, passage_ids=[row.listing_id for row in rows],
                        rankable=False, retrieval_path=retrieval_path, listing_cards=cards)
