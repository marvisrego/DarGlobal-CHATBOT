import os
from importlib.util import find_spec
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

from corpus_snapshot import CorpusUnavailable, live_snapshot, load_snapshot


class PostgresCorpusTests(unittest.TestCase):
    @unittest.skipUnless(find_spec("psycopg"), "Postgres dependency is optional")
    def test_answer_reads_current_postgres_fields_without_using_snapshot_text(self):
        from corpus_postgres import PostgresCorpus
        from property_search import search

        snapshot = live_snapshot("v2")
        row = load_snapshot(snapshot)["guide:dammam-badiyah"]
        row["metadata"]["points"] = ["Postgres evidence for Al Badiyah."]
        source = PostgresCorpus("host=localhost dbname=test", snapshot)
        source._ids = [row["record_id"]]
        connection = MagicMock()
        connection.execute.return_value.fetchall.return_value = [row]
        with patch("corpus_postgres.connect") as connect:
            connect.return_value.__enter__.return_value = connection
            result = search("What does Wasalt say about Al Badiyah?", snapshot=snapshot.with_name("missing.jsonl"),
                            live_data=True, record_loader=source)
        self.assertEqual(result.evidence[0].claims, ("Postgres evidence for Al Badiyah.",))
        self.assertEqual(result.sources[0].url, row["canonical_url"])
        self.assertEqual(connection.execute.call_args_list[0].args, ("SET TRANSACTION READ ONLY",))
        self.assertEqual(connection.execute.call_args_list[-1].args[1], (["guide:dammam-badiyah"],))

    def test_database_failure_returns_503_without_snapshot_fallback(self):
        with patch.dict(os.environ, CHAT_MODE="mock", PROPERTY_DATA_MODE="live", PROPERTY_CORPUS_BACKEND="jsonl"):
            import main
        with (
            patch.object(main, "CORPUS_BACKEND", "postgres"),
            patch.object(main, "postgres_corpus", side_effect=CorpusUnavailable("incomplete_release")),
            self.assertLogs("uvicorn.error.darglobal", level="WARNING") as logs,
            TestClient(main.app) as client,
        ):
            response = client.post("/api/chat", json={"message": "Where is AIDA?"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["x-corpus-backend"], "postgres")
        self.assertEqual(response.json(), {"detail": "The property database is temporarily unavailable."})
        self.assertIn("incomplete_release", logs.output[0])
