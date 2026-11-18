"""Pinned local embeddings and the shared Qdrant vector configuration."""

from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import models


MODEL = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
DIMENSION = 384
EMBEDDING = {
    "model": MODEL,
    "revision": REVISION,
    "pipeline": "onnx/model.onnx; mean pooling; L2 normalization; 256 tokens",
}


def corpus_point_id(record_id):
    return str(uuid5(NAMESPACE_URL, f"https://github.com/marvisrego/DarGlobal-CHATBOT#{record_id}"))


class Embedder:
    def __init__(self, *, local_files_only=False):
        from huggingface_hub import hf_hub_download
        from onnxruntime import InferenceSession, SessionOptions
        from tokenizers import Tokenizer

        download_options = {"revision": REVISION, "token": False, "local_files_only": local_files_only}
        tokenizer_path = hf_hub_download(MODEL, "tokenizer.json", **download_options)
        model_path = hf_hub_download(MODEL, "onnx/model.onnx", **download_options)
        self.tokenizer = Tokenizer.from_file(tokenizer_path)
        self.tokenizer.enable_truncation(max_length=256)
        self.tokenizer.enable_padding()
        options = SessionOptions()
        options.intra_op_num_threads = 1
        self.session = InferenceSession(model_path, options, providers=["CPUExecutionProvider"])

    def encode(self, texts):
        import numpy as np

        tokens = self.tokenizer.encode_batch(texts)
        mask = np.asarray([item.attention_mask for item in tokens], dtype=np.int64)
        hidden, = self.session.run(None, {
            "input_ids": np.asarray([item.ids for item in tokens], dtype=np.int64),
            "attention_mask": mask,
            "token_type_ids": np.asarray([item.type_ids for item in tokens], dtype=np.int64),
        })
        pooled = (hidden * mask[..., None]).sum(axis=1) / mask.sum(axis=1, keepdims=True)
        norms = np.linalg.norm(pooled, axis=1, keepdims=True)
        if pooled.shape != (len(texts), DIMENSION) or not np.isfinite(pooled).all() or np.any(norms == 0):
            raise ValueError("The embedding model returned invalid vectors.")
        return (pooled / norms).astype(np.float32).tolist()


def compatible_collection(config, *, dimension=DIMENSION, embedding=EMBEDDING):
    vectors = config.params.vectors
    return (
        isinstance(vectors, models.VectorParams)
        and vectors.size == dimension
        and vectors.distance == models.Distance.COSINE
        and vectors.datatype in {None, models.Datatype.FLOAT32}
        and vectors.multivector_config is None
        and (config.metadata or {}).get("corpus_embedding") == embedding
    )


def qdrant_endpoint(value):
    url = urlsplit(value)
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError("Set QDRANT_URL to an HTTPS endpoint without embedded credentials.")
    return url.geturl()
