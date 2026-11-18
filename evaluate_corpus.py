"""Score retrieval, response facts and citations as separate checks."""

import argparse
import json
import os
from pathlib import Path
from statistics import median, quantiles
from time import perf_counter
from unittest.mock import patch

from fastapi.testclient import TestClient

from property_search import LIVE_SNAPSHOT, search

ROOT = Path(__file__).resolve().parent
LIVE_GUIDE_CASE = {
    "id": "offplan",
    "question": "For Wasalt off-plan payments, compare staged payments with the developer track record for off-plan risk.",
    "expected": ["guide:offplan-payment", "guide:offplan-risk"],
    "facts": ["staged payments", "developer's track record"],
    "support": [],
}
RECOVERY_FACTS = {
    "tierra-typo": ["Benahavís, Spain", "Villas"],
    "paperwork-paraphrase": ["title deed", "building permit", "completion certificate"],
    "commute-paraphrase": ["commute", "before signing a lease"],
    "remote-paraphrase": ["maintenance", "cleaning"],
    "astera-typo": ["Ras Al Khaimah, UAE", "Beachfront residences"],
    "les-vagues-typo": ["Lusail, Qatar", "Apartments and retail units"],
}


def citation_key(source):
    return source["publisher"], source["title"], source["url"]


def normalized_fact(text):
    return " ".join(text.casefold().translate(str.maketrans("‘’‑–", "''--")).split())


def benchmark_embeddings():
    from property_vectors import Embedder

    cases = json.loads((ROOT / "evaluation/qdrant-queries.json").read_text(encoding="utf-8"))
    start = perf_counter()
    embedder = Embedder(local_files_only=True)
    ready = perf_counter()
    embedder.encode([cases[0]["question"]])
    first = perf_counter()
    print(f"Cached model initialization {(ready - start) * 1000:.1f} ms")
    print(f"First query embedding {(first - ready) * 1000:.2f} ms")
    for label, questions in (
        ("Ordinary", [case["question"] for case in cases]),
        ("Long", [("Which records describe property ownership, location, and availability? " * 30).strip()]),
    ):
        elapsed = []
        for question in questions * (60 // len(questions)):
            start = perf_counter()
            embedder.encode([question])
            elapsed.append((perf_counter() - start) * 1000)
        p95 = quantiles(elapsed, n=100)[94]
        print(f"{label} query embedding ({len(elapsed)} runs), p50 {median(elapsed):.2f} ms, p95 {p95:.2f} ms")


def main():
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--live", "--openrouter", dest="live", action="store_true", help="Run only the two live model checks")
    group.add_argument("--benchmark", action="store_true", help="Measure local query encoding only")
    retrieval = parser.add_mutually_exclusive_group()
    retrieval.add_argument("--hybrid", action="store_true", help="Use MiniLM Qdrant retrieval and include recovery cases")
    retrieval.add_argument("--nvidia", action="store_true", help="Use hosted NVIDIA retrieval and include recovery cases")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.benchmark:
        benchmark_embeddings()
        return 0
    os.environ.update(
        CHAT_MODE="live" if args.live else "mock", PROPERTY_DATA_MODE="live",
        PROPERTY_RETRIEVAL_MODE="nvidia" if args.nvidia else "hybrid" if args.hybrid else "jsonl",
        CHAT_PROVIDER="nvidia" if args.nvidia else "openrouter",
    )
    from main import app

    questions = json.loads((ROOT / "evaluation/questions.json").read_text(encoding="utf-8"))
    if args.hybrid or args.nvidia:
        additional = json.loads((ROOT / "evaluation/qdrant-queries.json").read_text(encoding="utf-8"))
        additional = [{**item, "facts": RECOVERY_FACTS[item["id"]]} for item in additional]
        if args.live:
            questions = [item for item in additional if item["id"] in {"tierra-typo", "paperwork-paraphrase"}]
        else:
            new = json.loads((ROOT / "evaluation/hybrid-queries.json").read_text(encoding="utf-8"))
            questions += additional + new
    elif args.live:
        questions = [next(item for item in questions if item["id"] == "compare"), LIVE_GUIDE_CASE]
    records = {
        row["record_id"]: row
        for line in LIVE_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("record_id")
    }
    results = []
    retrieved = []

    def trace_search(*args, **kwargs):
        result = search(*args, **kwargs)
        retrieved.append(result)
        return result

    with TestClient(app) as client, patch("main.search", side_effect=trace_search):
        for item in questions:
            start = perf_counter()
            response = client.post("/api/chat", json={"message": item["question"]})
            elapsed = perf_counter() - start
            top_five = retrieved.pop().passage_ids
            expected = set(item["expected"])
            retrieval_pass = expected.issubset(top_five) if expected else not top_five
            payload = response.json()
            answer = payload.get("answer", "")
            expected_citations = {
                (row["publisher"], row.get("section") or row["title"], row["canonical_url"])
                for key in expected if (row := records.get(key))
            }
            for key in expected:
                for source in records.get(key, {}).get("supporting_sources", []):
                    if source["publisher"] in item.get("support", []):
                        expected_citations.add((source["publisher"], source["title"], source["canonical_url"]))
            actual_sources = payload.get("sources", [])
            citation_pass = (
                response.is_success and all(not source["fixture"] and source["url"] for source in actual_sources)
                and {citation_key(source) for source in actual_sources} == expected_citations
            )
            fact_pass = response.is_success and all(
                normalized_fact(fact) in normalized_fact(answer) for fact in item["facts"]
            )
            if args.live:
                fact_pass = fact_pass and bool(payload.get("inference_model"))
            results.append({
                "id": item["id"], "top_five": top_five, "retrieval_pass": retrieval_pass,
                "facts_pass": fact_pass, "citations_pass": citation_pass, "status": response.status_code,
                "inference_model": payload.get("inference_model"),
                "inference_provider": payload.get("inference_provider"),
                "retrieval_path": payload.get("retrieval_path"),
                "seconds": round(elapsed, 4), "citations": actual_sources, "error": payload.get("detail"),
            })
    for key in ("retrieval_pass", "facts_pass", "citations_pass"):
        print(f"{key}: {sum(result[key] for result in results)}/{len(results)}")
    for row in results:
        print(row["id"], row["status"], ", ".join(row["top_five"]) or "(no passage)",
              row["inference_model"] or row["error"] or "")
    if args.output:
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return int(not all(row[key] for row in results for key in ("retrieval_pass", "facts_pass", "citations_pass")))


if __name__ == "__main__":
    raise SystemExit(main())
