"""Query a private listing release and rank only Postgres-eligible units."""

from contextlib import closing

from psycopg import Error, connect
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from app.data.corpus_snapshot import CorpusUnavailable
from app.search.hybrid_retrieval import HybridLookup
from app.data.listing_pilot import (
    LISTING_FIELDS, LISTING_RESULT_LIMIT, Listing, ListingFilters, ListingQuery, collection_name, covered_city,
    listing_result, parse_listing_query,
)
from app.chat import nvidia_api
from app.search.property_search import SearchResult, normalized


TABLE = "public.property_listing"
RELEASES = "public.property_listing_release"
ROW_FIELDS = ("listing_id", "city_key", *LISTING_FIELDS)


def database_settings(dsn):
    settings = conninfo_to_dict(dsn)
    if settings.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        settings["sslmode"] = "require"
    return {**settings, "connect_timeout": 5}


def select_listings(connection, release, query):
    conditions = ["release = %s"]
    params = [release]
    for field, value in (
        ("city_key", query.city), ("bedrooms", query.bedrooms), ("currency", query.currency), ("source", query.source),
        ("transaction_type", query.transaction_type), ("rent_period", query.rent_period),
    ):
        if value is not None:
            conditions.append(f"{field} = %s")
            params.append(value)
    if query.types:
        conditions.append("lower(property_type) = ANY(%s)")
        params.append(list(query.types))
    if query.budget is not None:
        conditions.append("asking_price < %s" if query.strict else "asking_price <= %s")
        params.append(query.budget)
    if query.minimum_price is not None:
        conditions.append("asking_price >= %s")
        params.append(query.minimum_price)
    sort = "asking_price NULLS LAST, listing_id"
    city_order = f"row_number() OVER (PARTITION BY city_key ORDER BY {sort}), " if not query.city else ""
    return connection.execute(
        f"SELECT {', '.join(LISTING_FIELDS)} FROM {TABLE} WHERE {' AND '.join(conditions)} "
        f"ORDER BY {city_order}{sort} LIMIT 100",
        params,
    ).fetchall()


def vector_config(digest):
    return {
        "dimension": nvidia_api.DIMENSION,
        "embedding": {**nvidia_api.EMBEDDING, "listing_release_sha256": digest},
    }


def resolve_filters(message, cities, options, previous):
    supplied = options.model_dump(exclude_none=True, exclude={"refine_previous", "cheapest_previous"})
    filters = previous.model_dump(exclude_none=True) if previous and options.refine_previous else {}
    filters.update(supplied)
    explicit = parse_listing_query(message, cities, require_city=False)
    fields = {
        "city": explicit.city, "bedrooms": explicit.bedrooms, "currency": explicit.currency,
        "minimum_price": explicit.minimum_price, "maximum_price": explicit.budget,
        "strict_maximum": explicit.strict if explicit.budget is not None else None,
        "publisher": explicit.source, "transaction_type": explicit.transaction_type,
        "property_types": list(explicit.types) or None, "rent_period": explicit.rent_period,
        "preference": explicit.preference[:250] if explicit.preference else None,
    }
    filters.update({key: value for key, value in fields.items() if value is not None})
    if city := filters.get("city"):
        filters["city"] = covered_city(city, cities) or city
    if options.maximum_price is not None and options.strict_maximum is None and explicit.budget is None:
        filters["strict_maximum"] = False
    context = ListingFilters.model_validate(filters)
    if context.maximum_price is not None and context.minimum_price is not None and context.minimum_price > context.maximum_price:
        raise ValueError("The minimum budget must be at or below the maximum.")
    priced = context.maximum_price is not None or context.minimum_price is not None
    if priced and not context.currency:
        raise ValueError("Which currency should I use for your budget?")
    if priced and context.transaction_type == "rent" and not context.rent_period:
        raise ValueError("Is your rental budget monthly or yearly?")
    return ListingQuery(
        city=normalized(context.city) if context.city else None, bedrooms=context.bedrooms, currency=context.currency,
        budget=context.maximum_price, minimum_price=context.minimum_price, strict=bool(context.strict_maximum),
        source=context.publisher, transaction_type=context.transaction_type, types=tuple(context.property_types or ()),
        rent_period=context.rent_period, preference=context.preference,
    ), context


def search_listings(connection, release, message, lookup_for_release, *, previous_ids=(), options=None, previous=None):
    registered = connection.execute(f"SELECT digest FROM {RELEASES} WHERE version = %s", (release,)).fetchone()
    if registered is None:
        raise CorpusUnavailable("missing_listing_release")
    if previous_ids:
        priced = [Listing.model_validate(row) for row in connection.execute(
            f"SELECT {', '.join(LISTING_FIELDS)} FROM {TABLE} "
            "WHERE release = %s AND listing_id = ANY(%s) AND asking_price IS NOT NULL AND currency IS NOT NULL "
            "ORDER BY asking_price, listing_id",
            (release, list(previous_ids)),
        )]
        if not priced:
            return SearchResult("The previous listings have no verified comparable asking prices.", retrieval_path="postgres")
        if (len({(row.currency, row.transaction_type, row.rent_period) for row in priced}) > 1
                or any(row.transaction_type == "rent" and not row.rent_period for row in priced)):
            return SearchResult("Choose one currency and a sale or rental price period to compare.", retrieval_path="postgres")
        cheapest = [row for row in priced if row.asking_price == priced[0].asking_price]
        result = listing_result(cheapest, "postgres")
        result.message = "Lowest published asking price among the previous results."
        return result
    cities = [row["city_key"] for row in connection.execute(
        f"SELECT DISTINCT city_key FROM {TABLE} WHERE release = %s AND city_key IS NOT NULL", (release,)
    )]
    context = None
    try:
        if options is None:
            query = parse_listing_query(message, cities)
        else:
            query, context = resolve_filters(message, cities, options, previous)
    except ValueError as error:
        return SearchResult(str(error), retrieval_path="postgres")
    rows = [Listing.model_validate(row) for row in select_listings(connection, release, query)]
    retrieval_path = "postgres"
    if rows and query.preference:
        eligible = {row.listing_id: row for row in rows}
        with closing(lookup_for_release(registered["digest"])) as lookup:
            hits = lookup(query.preference, eligible_ids=set(eligible))
        ranked = {
            listing_id: eligible[listing_id] for listing_id, _ in (hits or ()) if listing_id in eligible
        }
        if ranked:
            rows = [*ranked.values(), *(row for row in rows if row.listing_id not in ranked)]
            retrieval_path = "qdrant"
        else:
            retrieval_path = "postgres_after_qdrant_failure"
    result = listing_result(rows[:LISTING_RESULT_LIMIT], retrieval_path, has_preference=bool(query.preference))
    result.listing_context = context
    return result


class PostgresListings:
    def __init__(self, dsn, release, embedder, *, shared_collection=None):
        try:
            self._settings = database_settings(dsn)
        except Error:
            raise RuntimeError("DATABASE_URL is not a valid Postgres connection setting.") from None
        self._release = release
        self._collection = shared_collection or collection_name(release)
        self._metadata_key = "listing_embedding" if shared_collection else "corpus_embedding"
        self._embedder = embedder

    def _lookup(self, digest):
        return HybridLookup(collection=self._collection, limit=LISTING_RESULT_LIMIT, embedder=self._embedder,
                            vector_config={**vector_config(digest), "metadata_key": self._metadata_key},
                            cooldown=0, structured_backend="Postgres")

    def __call__(self, message, *, previous_ids=(), options=None, previous=None):
        try:
            with connect(**self._settings, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL statement_timeout = '4s'")
                return search_listings(connection, self._release, message, self._lookup,
                                       previous_ids=previous_ids, options=options, previous=previous)
        except Error:
            raise CorpusUnavailable("listing_database_read_failed") from None
        except ValueError:
            raise CorpusUnavailable("invalid_listing_release") from None
