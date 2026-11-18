"""Copy the verified NVIDIA corpus and listing releases into one shared Qdrant collection."""

import argparse
from contextlib import closing
from math import isclose
import os

from dotenv import load_dotenv
from psycopg import connect
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from app.data.corpus_snapshot import RELEASES, ROOT, live_snapshot
from scripts.retrieval.index_qdrant import load_payloads, read_payloads
from app.data.listing_pilot import collection_name
from app.data.listing_store import RELEASES as LISTING_RELEASES, TABLE, database_settings, vector_config
from app.chat import nvidia_api
from app.search.property_vectors import compatible_collection, corpus_point_id, qdrant_endpoint


def checked_points(client, name, expected):
    points, offset = client.scroll(name, limit=len(expected) + 1, with_payload=True, with_vectors=True)
    by_id = {str(point.id): point for point in points}
    if (offset is not None or {key: point.payload for key, point in by_id.items()} != expected
            or client.count(name, exact=True).count != len(expected)):
        raise ValueError("Qdrant IDs or payloads differ from the verified release.")
    if any(not isinstance(point.vector, list) or len(point.vector) != nvidia_api.DIMENSION for point in points):
        raise ValueError("Qdrant returned an unexpected vector format.")
    return by_id


def combine(client, corpus, listing, digest, listing_ids):
    name = nvidia_api.shared_collection_name(corpus, listing)
    corpus_name = nvidia_api.collection_for_release(corpus)
    listing_name = collection_name(listing)
    corpus_config = nvidia_api.vector_config_for_release(corpus, live_snapshot(corpus))
    listing_config = vector_config(digest)
    for source, config in ((corpus_name, corpus_config), (listing_name, listing_config)):
        if not compatible_collection(client.get_collection(source).config, **config):
            raise ValueError("A source collection does not match its release metadata.")
    corpus_payloads = load_payloads(corpus)
    listing_payloads = {corpus_point_id(key): {"record_id": key} for key in listing_ids}
    if corpus_payloads.keys() & listing_payloads.keys():
        raise ValueError("Corpus and listing IDs overlap.")
    expected = {**corpus_payloads, **listing_payloads}
    original = {
        **checked_points(client, corpus_name, corpus_payloads),
        **checked_points(client, listing_name, listing_payloads),
    }
    if not client.collection_exists(name):
        try:
            client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=nvidia_api.DIMENSION, distance=models.Distance.COSINE),
                metadata={"corpus_embedding": corpus_config["embedding"], "listing_embedding": listing_config["embedding"]},
            )
        except UnexpectedResponse as error:
            if error.status_code != 409:
                raise
    config = client.get_collection(name).config
    if not (compatible_collection(config, **corpus_config)
            and compatible_collection(config, **listing_config, metadata_key="listing_embedding")):
        raise ValueError("The shared collection exists with incompatible release metadata.")
    read_payloads(client, name, expected)
    client.upsert(name, points=[
        models.PointStruct(id=key, vector=point.vector, payload=point.payload)
        for key, point in original.items()
    ], wait=True, ordering=models.WriteOrdering.STRONG)
    copied = checked_points(client, name, expected)
    if any(not isclose(value, other, abs_tol=1e-7)
           for key in original for value, other in zip(original[key].vector, copied[key].vector, strict=True)):
        raise ValueError("Shared vector read-back differs from its source vector.")
    return name, len(corpus_payloads), len(listing_payloads)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-version", choices=RELEASES, default="v2")
    parser.add_argument("--listing-release", default="v3")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env", interpolate=False)
    stage = "listing release read"
    try:
        collection_name(args.listing_release)
        with connect(**database_settings(os.environ["DATABASE_URL"])) as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            registered = connection.execute(
                f"SELECT digest FROM {LISTING_RELEASES} WHERE version = %s", (args.listing_release,)
            ).fetchone()
            listing_ids = [row[0] for row in connection.execute(
                f"SELECT listing_id FROM {TABLE} WHERE release = %s", (args.listing_release,)
            )]
        if not registered or not listing_ids:
            raise ValueError("The listing release is empty or unpublished.")
        stage = "shared collection creation and verification"
        with closing(QdrantClient(url=qdrant_endpoint(os.environ["QDRANT_URL"]),
                                  api_key=os.environ["QDRANT_API_KEY"], timeout=25,
                                  check_compatibility=False)) as client:
            name, corpus_count, listing_count = combine(
                client, args.corpus_version, args.listing_release, registered[0], listing_ids,
            )
        print(f"Verified {name}: {corpus_count} projects/guide passages and {listing_count} listings.")
    except Exception as error:
        parser.exit(1, f"{stage} failed ({type(error).__name__}).\n")


if __name__ == "__main__":
    main()
