"""Publish a verified, approved unit-listing export to a private versioned release."""

import argparse
from collections import Counter
from contextlib import closing
from hashlib import sha256
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from psycopg import connect
from psycopg.rows import dict_row
from qdrant_client import QdrantClient, models

from app.data.corpus_snapshot import ROOT
from scripts.retrieval.index_qdrant import ensure_collection, hosted_passage_vectors, read_payloads, verify
from app.data.listing_pilot import PUBLISHERS, Listing, collection_name
from app.data.listing_store import RELEASES, ROW_FIELDS, TABLE, database_settings, vector_config
from app.chat.nvidia_api import NvidiaRetrievalAPI
from app.search.property_vectors import corpus_point_id, qdrant_endpoint


MIGRATION = ROOT / "supabase/migrations/20261118180000_create_property_listings.sql"


def load_release(path, release):
    collection_name(release)
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or set(data) != {"approvals", "listings"}:
        raise ValueError("Use an object with approvals and listings.")
    approvals = data["approvals"]
    raw = data["listings"]
    limit = 10 if release == "v1" else 100
    if not isinstance(raw, list) or not 1 <= len(raw) <= limit:
        raise ValueError(f"{release} requires between 1 and {limit} verified unit listings.")
    if not isinstance(approvals, dict):
        raise ValueError("Record the reuse basis for each source.")
    listings = {}
    units = set()
    for position, item in enumerate(raw, start=1):
        try:
            listing = Listing.model_validate(item)
        except ValueError as error:
            raise ValueError(f"Invalid unit at row {position}. {error}") from None
        approval = approvals.get(listing.source)
        if not isinstance(approval, str) or not approval.strip():
            raise ValueError(f"Missing reuse basis for {listing.source}.")
        unit = listing.unit_key.casefold() if listing.unit_key else None
        if listing.listing_id in listings or (unit is not None and unit in units):
            raise ValueError(f"Duplicate unit at row {position}.")
        if unit is not None:
            units.add(unit)
        listings[listing.listing_id] = listing
    return [listings[key] for key in sorted(listings)]


def release_digest(listings):
    body = json.dumps(
        [listing.model_dump(mode="json") for listing in sorted(listings, key=lambda row: row.listing_id)],
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return sha256(body.encode("utf-8")).hexdigest()


def index_listings(client, embedder, release, digest, listings):
    collection = collection_name(release)
    ensure_collection(client, collection, create=True, **vector_config(digest))
    records = {corpus_point_id(row.listing_id): {"record_id": row.listing_id} for row in listings}
    actual = read_payloads(client, collection, records)
    if actual != records:
        payloads = {
            corpus_point_id(row.listing_id): {
                "text": ". ".join(filter(None, (row.property_type, row.district, row.city, *row.features))),
            }
            for row in listings
        }
        for key, payload in payloads.items():
            payload["text"] = payload["text"] or "Residential property unit"
        vectors = hosted_passage_vectors(embedder, digest, payloads)
        points = [
            models.PointStruct(id=point_id, vector=vector, payload=payload)
            for (point_id, payload), vector in zip(records.items(), vectors, strict=True)
        ]
        client.upsert(collection, points=points, wait=True, ordering=models.WriteOrdering.STRONG)
    verify(client, collection, records)


def stage_release(connection, release, digest, listings):
    connection.execute("SET LOCAL lock_timeout = '6s'")
    connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (TABLE,))
    connection.execute(MIGRATION.read_text(encoding="utf-8"), prepare=False)
    if release != "v1" and connection.execute(
        f"SELECT 1 FROM {RELEASES} WHERE version = 'v1'"
    ).fetchone() is None:
        raise ValueError("Publish and validate the initial v1 batch before publishing a larger release.")
    prior = connection.execute(f"SELECT digest FROM {RELEASES} WHERE version = %s", (release,)).fetchone()
    expected = {
        listing.listing_id: {
            "listing_id": listing.listing_id, "city_key": listing.city_key, **listing.model_dump(),
        }
        for listing in listings
    }
    if prior is None:
        connection.execute(f"INSERT INTO {RELEASES} (version, digest) VALUES (%s, %s)", (release, digest))
        names = ", ".join(ROW_FIELDS)
        values = ", ".join(f"%({field})s" for field in ROW_FIELDS)
        with connection.cursor() as cursor:
            cursor.executemany(
                f"INSERT INTO {TABLE} (release, {names}) VALUES (%(release)s, {values})",
                [{"release": release, **row} for row in expected.values()],
            )
    elif prior["digest"] != digest:
        raise ValueError("This release already has different data. Use a new version.")
    actual = connection.execute(
        f"SELECT {', '.join(ROW_FIELDS)} FROM {TABLE} WHERE release = %s", (release,)
    ).fetchall()
    if {row["listing_id"]: row for row in actual} != expected:
        raise ValueError("Postgres read-back differs from the verified input.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--release", default="v1")
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    stage = "validation"
    try:
        listings = load_release(args.input, args.release)
        digest = release_digest(listings)
        counts = Counter(row.source for row in listings)
        print(f"Validated {len(listings)} input rows. " + ", ".join(
            f"{publisher}: {counts[key]}" for key, publisher in PUBLISHERS.items()
        ), flush=True)
        if args.validate_only:
            return
        load_dotenv(ROOT / ".env", interpolate=False)
        names = ("DATABASE_URL", "QDRANT_URL", "QDRANT_API_KEY", "Nvidia_Embed")
        settings = {name: os.getenv(name, "").strip() for name in names}
        missing = [name for name, value in settings.items() if not value]
        if missing:
            raise ValueError(f"Set {', '.join(missing)} in the environment.")
        stage = "Qdrant publication"
        with (
            closing(QdrantClient(
                url=qdrant_endpoint(settings["QDRANT_URL"]), api_key=settings["QDRANT_API_KEY"],
                timeout=20, check_compatibility=False,
            )) as client,
            closing(NvidiaRetrievalAPI(settings["Nvidia_Embed"], timeout=10)) as embedder,
        ):
            index_listings(client, embedder, args.release, digest, listings)
        stage = "Postgres publication"
        with connect(**database_settings(settings["DATABASE_URL"]), row_factory=dict_row) as connection:
            stage_release(connection, args.release, digest, listings)
        print(f"Verified {len(listings)} listings in Postgres and {collection_name(args.release)}.", flush=True)
    except ValueError as error:
        if stage == "validation":
            parser.exit(1, f"{stage} failed. {error}\n")
        parser.exit(1, f"{stage} failed (invalid input or stored release).\n")
    except Exception as error:
        parser.exit(1, f"{stage} failed ({type(error).__name__}).\n")


if __name__ == "__main__":
    main()
