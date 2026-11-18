"""NVIDIA-hosted embedding and reranking API clients."""

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from time import sleep

import httpx

from corpus_snapshot import snapshot_digest
CHAT_MODEL = "nvidia/nemotron-3-ultra-550b-a55b"
CHAT_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
EMBED_MODEL = "nvidia/nemotron-3.5-embed-1b"
EMBED_ENDPOINT = "https://integrate.api.nvidia.com/v1/embeddings"
RERANK_MODEL = "nvidia/nemotron-3.5-rerank-8b"
RERANK_ENDPOINT = f"https://ai.api.nvidia.com/v1/retrieval/{RERANK_MODEL}/reranking"
DIMENSION = 2048
COLLECTION = "darglobal-wasalt-nvidia-embed-1b-v1"
EMBEDDING = {
    "model": EMBED_MODEL,
    "dimension": DIMENSION,
    "passage_input_type": "passage",
    "query_input_type": "query",
    "encoding_format": "float",
}
RERANK_LIMIT = 8
LOGGER = logging.getLogger("uvicorn.error.darglobal")


def collection_for_release(version):
    return f"{COLLECTION.rsplit('-', 1)[0]}-{version}"


def vector_config_for_release(version, snapshot):
    embedding = EMBEDDING
    if version != "v1":
        embedding = {**EMBEDDING, "snapshot_sha256": snapshot_digest(snapshot)}
    return {"dimension": DIMENSION, "embedding": embedding}


class NvidiaRetrievalAPI:
    def __init__(self, embed_key, rerank_key="", *, timeout=8.0):
        self._embed_key = embed_key
        self._rerank_key = rerank_key
        self._client = httpx.Client(http2=True, timeout=httpx.Timeout(timeout, connect=3.0))

    def _post(self, endpoint, key, payload):
        response = self._client.post(endpoint, json=payload, headers={"Authorization": f"Bearer {key}"})
        response.raise_for_status()
        return response.json()

    def encode(self, texts, *, input_type="query"):
        batches = [texts[start:start + 4] for start in range(0, len(texts), 4)]
        encode_batch = partial(self._encode_batch, input_type=input_type)
        if input_type == "passage" and len(batches) > 1:
            with ThreadPoolExecutor(max_workers=3) as pool:
                return [vector for vectors in pool.map(encode_batch, batches) for vector in vectors]
        return [vector for batch in batches for vector in encode_batch(batch)]

    def _encode_batch(self, batch, *, input_type):
        payload = {
            "model": EMBED_MODEL,
            "input": batch,
            "input_type": input_type,
            "encoding_format": "float",
            "dimensions": DIMENSION,
        }
        try:
            response = self._post(EMBED_ENDPOINT, self._embed_key, payload)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as error:
            if isinstance(error, httpx.HTTPStatusError) and (
                input_type == "query" or (error.response.status_code < 500 and error.response.status_code != 429)
            ):
                raise
            if input_type == "passage" and len(batch) > 1 and not isinstance(error, httpx.HTTPStatusError):
                middle = len(batch) // 2
                return self._encode_batch(batch[:middle], input_type=input_type) + self._encode_batch(
                    batch[middle:], input_type=input_type
                )
            sleep(0.4)
            if len(batch) == 1:
                payload["input"] = batch[0]
            response = self._post(EMBED_ENDPOINT, self._embed_key, payload)
        if response["model"] != EMBED_MODEL:
            raise ValueError("Unexpected embedding model.")
        items = sorted(response["data"], key=lambda item: item["index"])
        if [item["index"] for item in items] != list(range(len(batch))):
            raise ValueError("Invalid embedding indexes.")
        vectors = [item["embedding"] for item in items]
        if any(len(vector) != DIMENSION or not all(math.isfinite(value) for value in vector) or not any(vector)
               for vector in vectors):
            raise ValueError("Invalid hosted embedding vector.")
        return vectors

    def rerank(self, message, passages):
        try:
            response = self._post(RERANK_ENDPOINT, self._rerank_key, {
                "model": RERANK_MODEL,
                "query": {"text": message},
                "passages": [{"text": text} for text in passages],
            })
            rankings = response["rankings"]
            indexes = [item["index"] for item in rankings]
            if (
                any(type(index) is not int for index in indexes)
                or sorted(indexes) != list(range(len(passages)))
                or any(not math.isfinite(item["logit"]) for item in rankings)
            ):
                raise ValueError("Invalid hosted reranking response.")
            return [item["index"] for item in sorted(rankings, key=lambda item: (-item["logit"], item["index"]))]
        except (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as error:
            LOGGER.warning("NVIDIA rerank unavailable (%s); preserving candidate order.", type(error).__name__)
            return list(range(len(passages)))

    def close(self):
        self._client.close()
