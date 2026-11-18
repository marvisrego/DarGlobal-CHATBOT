"""Score retrieval, response facts and citations as separate checks."""

import argparse
import json
import os
from pathlib import Path

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


def citation_key(source):
    return source["publisher"], source["title"], source["url"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--openrouter", action="store_true", help="Run only the two live model checks")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    os.environ.update(CHAT_MODE="live" if args.openrouter else "mock", PROPERTY_DATA_MODE="live")
    from main import app

    questions = json.loads((ROOT / "evaluation/questions.json").read_text(encoding="utf-8"))
    if args.openrouter:
        questions = [next(item for item in questions if item["id"] == "compare"), LIVE_GUIDE_CASE]
    records = {
        row["record_id"]: row
        for line in LIVE_SNAPSHOT.read_text(encoding="utf-8").splitlines()
        if (row := json.loads(line)).get("record_id")
    }
    results = []
    with TestClient(app) as client:
        for item in questions:
            retrieved = search(item["question"], live_data=True)
            top_five = retrieved.passage_ids
            expected = set(item["expected"])
            retrieval_pass = expected.issubset(top_five) if expected else not top_five
            response = client.post("/api/chat", json={"message": item["question"]})
            payload = response.json()
            answer = payload.get("answer", "")
            expected_citations = {
                (row["publisher"], row.get("section") or row["title"], row["canonical_url"])
                for key in expected if (row := records.get(key))
            }
            for key in expected:
                for source in records.get(key, {}).get("supporting_sources", []):
                    if source["publisher"] in item["support"]:
                        expected_citations.add((source["publisher"], source["title"], source["canonical_url"]))
            actual_sources = payload.get("sources", [])
            citation_pass = (
                response.is_success and all(not source["fixture"] and source["url"] for source in actual_sources)
                and {citation_key(source) for source in actual_sources} == expected_citations
            )
            fact_pass = response.is_success and all(fact.casefold() in answer.casefold() for fact in item["facts"])
            if args.openrouter:
                fact_pass = fact_pass and bool(payload.get("inference_model"))
            results.append({
                "id": item["id"], "top_five": top_five, "retrieval_pass": retrieval_pass,
                "facts_pass": fact_pass, "citations_pass": citation_pass, "status": response.status_code,
                "inference_model": payload.get("inference_model"),
                "citations": actual_sources, "error": payload.get("detail"),
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
