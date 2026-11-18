"""Read-only vector lookup for the opt-in chatbot mode."""

import logging
import os
from threading import Lock
from time import monotonic


COLLECTION = "darglobal-wasalt-minilm-v1"
LOGGER = logging.getLogger(__name__)
RETRY_AFTER_SECONDS = 30


class HybridLookup:
    def __init__(self, *, collection=COLLECTION, limit=5, embedder=None, vector_config=None,
                 cooldown=RETRY_AFTER_SECONDS):
        self._lock = Lock()
        self._retry_at = 0
        self._client = None
        self._embedder = embedder
        self._collection = collection
        self._limit = limit
        self._vector_config = vector_config or {}
        self._cooldown = cooldown

    def _initialize(self):
        from qdrant_client import QdrantClient

        from property_vectors import Embedder, compatible_collection, qdrant_endpoint

        url = qdrant_endpoint(os.environ.get("QDRANT_URL", "").strip())
        key = os.environ.get("QDRANT_API_KEY", "").strip()
        if not key:
            raise ValueError("QDRANT_API_KEY is missing.")
        client = QdrantClient(url=url, api_key=key, timeout=2, check_compatibility=False)
        try:
            if not compatible_collection(client.get_collection(self._collection).config, **self._vector_config):
                raise ValueError("Incompatible Qdrant collection.")
            if self._embedder is None:
                self._embedder = Embedder(local_files_only=True)
        except Exception:
            client.close()
            raise
        self._client = client

    def __call__(self, message, *, eligible_ids=None):
        stage = "collection initialization"
        try:
            from qdrant_client import models
            from property_vectors import corpus_point_id

            with self._lock:
                if monotonic() < self._retry_at:
                    return None
                if self._client is None:
                    self._initialize()
                client = self._client
            stage = "query embedding"
            vector, = self._embedder.encode([message])
            stage = "Qdrant query"
            filters = None
            if eligible_ids is not None:
                filters = models.Filter(must=[models.HasIdCondition(
                    has_id=[corpus_point_id(record_id) for record_id in sorted(eligible_ids)]
                )])
            points = client.query_points(
                self._collection, query=vector, limit=self._limit, with_payload=["record_id"],
                search_params=models.SearchParams(exact=True), query_filter=filters,
            ).points
            return [
                (point.payload["record_id"], point.score)
                for point in points if point.payload and isinstance(point.payload.get("record_id"), str)
            ]
        except Exception as error:
            with self._lock:
                if monotonic() >= self._retry_at:
                    self._retry_at = monotonic() + self._cooldown
                    # Exception messages may contain URLs, headers, or response bodies.
                    LOGGER.warning("Hybrid retrieval unavailable at %s (%s); using JSONL retrieval.",
                                   stage, type(error).__name__)
            return None

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None
