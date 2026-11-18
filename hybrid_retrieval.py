"""Read-only vector lookup for the opt-in chatbot mode."""

import logging
import os
from threading import Lock
from time import monotonic


COLLECTION = "darglobal-wasalt-minilm-v1"
LOGGER = logging.getLogger(__name__)
RETRY_AFTER_SECONDS = 30


class HybridLookup:
    def __init__(self):
        self._lock = Lock()
        self._retry_at = 0
        self._client = None
        self._embedder = None

    def _initialize(self):
        from qdrant_client import QdrantClient

        from property_vectors import Embedder, compatible_collection, qdrant_endpoint

        url = qdrant_endpoint(os.environ.get("QDRANT_URL", "").strip())
        key = os.environ.get("QDRANT_API_KEY", "").strip()
        if not key:
            raise ValueError("QDRANT_API_KEY is missing.")
        client = QdrantClient(url=url, api_key=key, timeout=2, check_compatibility=False)
        try:
            if not compatible_collection(client.get_collection(COLLECTION).config):
                raise ValueError("Incompatible Qdrant collection.")
            if self._embedder is None:
                self._embedder = Embedder(local_files_only=True)
        except Exception:
            client.close()
            raise
        self._client = client

    def __call__(self, message):
        try:
            from qdrant_client import models

            with self._lock:
                if monotonic() < self._retry_at:
                    return []
                if self._client is None:
                    self._initialize()
                client = self._client
            vector, = self._embedder.encode([message])
            points = client.query_points(
                COLLECTION, query=vector, limit=5, with_payload=["record_id"],
                search_params=models.SearchParams(exact=True),
            ).points
            return [
                (point.payload["record_id"], point.score)
                for point in points if point.payload and isinstance(point.payload.get("record_id"), str)
            ]
        except Exception as error:
            with self._lock:
                if monotonic() >= self._retry_at:
                    self._retry_at = monotonic() + RETRY_AFTER_SECONDS
                    # Exception messages may contain URLs, headers, or response bodies.
                    LOGGER.warning("Hybrid retrieval unavailable (%s); using JSONL retrieval.", type(error).__name__)
            return []

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
