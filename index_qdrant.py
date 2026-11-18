"""Index or evaluate the verified property corpus in Qdrant."""

import argparse
from collections import Counter
from contextlib import ExitStack, closing
from functools import partial
import json
from pathlib import Path
import re

from dotenv import dotenv_values
from qdrant_client import QdrantClient, models
from qdrant_client.http.exceptions import UnexpectedResponse

from corpus_snapshot import ROOT, SNAPSHOT, load_snapshot
from property_search import search, valid_citation
from property_vectors import (
    DIMENSION, EMBEDDING, MODEL, REVISION, Embedder, compatible_collection, corpus_point_id, qdrant_endpoint,
)
import nvidia_api


LOCAL_COLLECTION = "darglobal-wasalt-minilm-v1"
EVALUATION = ROOT / "evaluation"
SETTINGS = ("QDRANT_URL", "QDRANT_API_KEY", "QDRANT_COLLECTION")


class IndexingError(Exception):
    """An operational error whose message is safe to print."""


def load_payloads():
    rows = load_snapshot(SNAPSHOT)
    counts = Counter((row["publisher"], row["record_type"]) for row in rows.values())
    guides = {row["canonical_url"] for row in rows.values() if row["record_type"] == "buyer_guide"}
    if counts != {("Dar Global PLC", "project"): 11, ("Wasalt", "buyer_guide"): 14} or len(guides) != 7:
        raise IndexingError("Expected 11 Dar Global PLC projects and 14 sections across seven Wasalt guides.")
    payloads = {}
    for record_id, row in rows.items():
        info = row["metadata"]
        if not valid_citation({**info, **row}) or not all(map(valid_citation, row["supporting_sources"])):
            raise IndexingError("The snapshot includes an invalid corpus citation.")
        point_id = corpus_point_id(record_id)
        payloads[point_id] = {
            "record_id": record_id,
            "record_type": row["record_type"],
            "title": row["title"],
            "text": row["searchable_text"],
            "publisher": row["publisher"],
            "source_url": row["source_url"],
            "citation": {
                "canonical_url": row["canonical_url"],
                "title": info.get("section") or row["title"],
                "source_page": info.get("source_page"),
                "source_as_of": row["source_as_of"].isoformat() if row["source_as_of"] else None,
                "collected_at": row["collected_at"].isoformat(),
                "collection_method": row["collection_method"],
                "supporting_sources": row["supporting_sources"],
            },
        }
    return payloads


def open_collection(args):
    if args.local:
        # Persistence lets two independent invocations exercise collection reuse.
        path = ROOT / "exports" / "qdrant-local"
        path.mkdir(parents=True, exist_ok=True)
        return QdrantClient(path=str(path)), LOCAL_COLLECTION
    values = dotenv_values(ROOT / ".env", interpolate=False)
    settings = {key: (values.get(key) or "").strip() for key in SETTINGS}
    if args.nvidia:
        settings["QDRANT_COLLECTION"] = nvidia_api.COLLECTION
    elif args.collection:
        settings["QDRANT_COLLECTION"] = args.collection
    missing = [key for key, value in settings.items() if not value]
    if missing:
        raise IndexingError(f"Fill in {', '.join(missing)} in local .env. Use --local to verify without credentials.")
    name = settings["QDRANT_COLLECTION"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,255}", name) or name in {".", ".."}:
        raise IndexingError("Use a valid Qdrant collection name in QDRANT_COLLECTION or --collection.")
    try:
        url = qdrant_endpoint(settings["QDRANT_URL"])
    except ValueError as error:
        raise IndexingError("Set QDRANT_URL in local .env to a valid HTTPS endpoint.") from error
    return QdrantClient(url=url, api_key=settings["QDRANT_API_KEY"], timeout=20,
                        check_compatibility=False), name


def ensure_collection(client, name, *, create, dimension=DIMENSION, embedding=EMBEDDING):
    created = False
    if not client.collection_exists(name):
        if not create:
            raise IndexingError("Collection is missing. Run the index command first.")
        try:
            created = client.create_collection(
                collection_name=name,
                vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE),
                metadata={"corpus_embedding": embedding},
            )
        except UnexpectedResponse as error:
            if error.status_code != 409:
                raise
    if not compatible_collection(client.get_collection(name).config, dimension=dimension, embedding=embedding):
        raise IndexingError("Collection has incompatible vector settings or embedding metadata. "
                            "Choose a separate collection via QDRANT_COLLECTION or --collection.")
    return created


def read_payloads(client, name, payloads):
    points, offset = client.scroll(name, limit=len(payloads) + 1, with_payload=True, with_vectors=False)
    actual = {str(point.id): point.payload for point in points}
    if offset is not None or any(
        point_id not in payloads or (payload or {}).get("record_id") != payloads[point_id]["record_id"]
        for point_id, payload in actual.items()
    ):
        raise IndexingError("Collection contains unrelated points or conflicting record IDs. Choose a separate collection.")
    return actual


def verify(client, name, payloads):
    actual = read_payloads(client, name, payloads)
    if actual != payloads or client.count(name, exact=True).count != len(payloads):
        raise IndexingError("Qdrant read-back did not match all 25 expected payloads.")
    samples = {"project:aida", "guide:buyer-checks"}
    for payload in actual.values():
        if payload["record_id"] in samples:
            print(f"Verified citation metadata for {payload['record_id']} ({payload['publisher']}).")


def evaluate(client, name, embedder):
    questions = json.loads((EVALUATION / "questions.json").read_text(encoding="utf-8"))
    extra = json.loads((EVALUATION / "qdrant-queries.json").read_text(encoding="utf-8"))
    baseline = {
        entry["id"]: entry["top_five"]
        for entry in json.loads((EVALUATION / "baseline.json").read_text(encoding="utf-8"))
    }
    cases = questions + extra
    vectors = embedder.encode([case["question"] for case in cases])
    results = []
    for case, vector in zip(cases, vectors, strict=True):
        points = client.query_points(name, query=vector, limit=5, with_payload=["record_id"],
                                     search_params=models.SearchParams(exact=True)).points
        rankings = {
            "qdrant": [point.payload["record_id"] for point in points],
            "current": search(case["question"], live_data=True).passage_ids,
        }
        if case["id"] in baseline:
            rankings["baseline"] = baseline[case["id"]]
        expected = set(case["expected"])
        passed = {key: expected.issubset(ids) if expected else not ids for key, ids in rankings.items()}
        results.append({**case, "top_five": rankings, "pass": passed,
                        "qdrant_scores": [round(point.score, 6) for point in points]})
    for label, group in (("Curated", results[:len(questions)]), ("New", results[len(questions):])):
        scores = ", ".join(
            f"{key} {sum(row['pass'][key] for row in group)}/{len(group)}" for key in group[0]["pass"]
        )
        print(f"{label} queries: {scores}.")
    for result in results:
        print(result["id"], ", ".join(result["top_five"]["qdrant"]))
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--dry-run", action="store_true", help="Validate the snapshot and embed locally")
    action.add_argument("--evaluate", action="store_true", help="Compare Qdrant with the recorded and current baselines")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--local", action="store_true", help="Use embedded Qdrant under ignored exports/")
    target.add_argument("--collection", help="Override QDRANT_COLLECTION for this invocation")
    target.add_argument("--nvidia", action="store_true", help="Use hosted NVIDIA embeddings and the dedicated collection")
    parser.add_argument("--output", type=Path, help="Write the evaluation trace as JSON")
    args = parser.parse_args()
    if args.output and not args.evaluate:
        parser.error("--output requires --evaluate")
    stage = "snapshot validation"
    resources = ExitStack()
    try:
        payloads = load_payloads()
        print("Validated 25 live passages: 11 DarGlobal projects and 14 sections across seven Wasalt guides.")
        stage = "embedding setup"
        config = {}
        if args.nvidia:
            key = (dotenv_values(ROOT / ".env", interpolate=False).get("Nvidia_Embed") or "").strip()
            if not key:
                raise IndexingError("Set Nvidia_Embed in local .env.")
            embedder = resources.enter_context(closing(nvidia_api.NvidiaRetrievalAPI(key, timeout=20.0)))
            passage_encode = partial(embedder.encode, input_type="passage")
            config = {"dimension": nvidia_api.DIMENSION, "embedding": nvidia_api.EMBEDDING}
            print(f"Embedding model: {nvidia_api.EMBED_MODEL}, expected {nvidia_api.DIMENSION} dimensions.")
        else:
            embedder = Embedder()
            passage_encode = embedder.encode
            print(f"Embedding model: {MODEL}@{REVISION}, {DIMENSION} dimensions.")
        stage = "passage embedding"
        vectors = None
        if args.dry_run or (args.nvidia and not args.evaluate):
            vectors = passage_encode([payload["text"] for payload in payloads.values()])
            print(f"Verified {len(vectors)} passage vectors with {len(vectors[0])} dimensions.")
        if args.dry_run:
            return
        stage = "Qdrant connection"
        client, name = open_collection(args)
        with closing(client):
            stage = "Qdrant collection validation"
            created = ensure_collection(client, name, create=not args.evaluate, **config)
            if args.evaluate:
                stage = "Qdrant evaluation"
                verify(client, name, payloads)
                results = evaluate(client, name, embedder)
            else:
                read_payloads(client, name, payloads)
                stage = "passage embedding"
                if vectors is None:
                    vectors = passage_encode([payload["text"] for payload in payloads.values()])
                points = [
                    models.PointStruct(id=point_id, vector=vector, payload=payload)
                    for (point_id, payload), vector in zip(payloads.items(), vectors, strict=True)
                ]
                stage = "Qdrant upsert and read-back"
                client.upsert(name, points=points, wait=True, ordering=models.WriteOrdering.STRONG)
                verify(client, name, payloads)
                print(f"{'Created' if created else 'Reused'} {name}. Verified exactly 25 points.")
        if args.output:
            stage = "evaluation output"
            args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except IndexingError as error:
        parser.exit(1, f"{error}\n")
    except UnexpectedResponse as error:
        parser.exit(1, f"{stage} failed with HTTP {error.status_code}.\n")
    except Exception as error:
        # Transport exceptions can contain request URLs, response bodies or headers.
        parser.exit(1, f"{stage} failed ({type(error).__name__}).\n")
    finally:
        resources.close()


if __name__ == "__main__":
    main()
