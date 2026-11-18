"""Query a private listing release and rank only Postgres-eligible units."""

from contextlib import closing

from psycopg import Error, connect
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row

from corpus_snapshot import CorpusUnavailable
from hybrid_retrieval import HybridLookup
from listing_pilot import LISTING_FIELDS, Listing, collection_name, listing_result, parse_listing_query
import nvidia_api
from property_search import SearchResult


TABLE = "public.property_listing"
RELEASES = "public.property_listing_release"
ROW_FIELDS = ("listing_id", "city_key", *LISTING_FIELDS)
RESULT_LIMIT = 5


def database_settings(dsn):
    settings = conninfo_to_dict(dsn)
    if settings.get("sslmode") not in {"require", "verify-ca", "verify-full"}:
        settings["sslmode"] = "require"
    return {**settings, "connect_timeout": 5}


def select_listings(connection, release, query):
    conditions = ["release = %s", "city_key = %s"]
    params = [release, query.city]
    for field, value in (
        ("bedrooms", query.bedrooms), ("currency", query.currency), ("source", query.source),
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
    return connection.execute(
        f"SELECT {', '.join(LISTING_FIELDS)} FROM {TABLE} WHERE {' AND '.join(conditions)} "
        "ORDER BY asking_price NULLS LAST, listing_id",
        params,
    ).fetchall()


def vector_config(digest):
    return {
        "dimension": nvidia_api.DIMENSION,
        "embedding": {**nvidia_api.EMBEDDING, "listing_release_sha256": digest},
    }


def search_listings(connection, release, message, lookup_for_release, *, previous_ids=()):
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
    try:
        query = parse_listing_query(message, cities)
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
            rows = list(ranked.values())
            retrieval_path = "qdrant"
        else:
            retrieval_path = "postgres_after_qdrant_failure"
    return listing_result(rows[:RESULT_LIMIT], retrieval_path, has_preference=bool(query.preference))


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
        return HybridLookup(collection=self._collection, limit=RESULT_LIMIT, embedder=self._embedder,
                            vector_config={**vector_config(digest), "metadata_key": self._metadata_key},
                            cooldown=0, structured_backend="Postgres")

    def __call__(self, message, *, previous_ids=()):
        try:
            with connect(**self._settings, row_factory=dict_row) as connection:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                connection.execute("SET LOCAL statement_timeout = '4s'")
                return search_listings(connection, self._release, message, self._lookup, previous_ids=previous_ids)
        except Error:
            raise CorpusUnavailable("listing_database_read_failed") from None
        except ValueError:
            raise CorpusUnavailable("invalid_listing_release") from None
