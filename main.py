import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from answers import ChatReply
from corpus_snapshot import RELEASES, CorpusUnavailable, live_snapshot
from listing_pilot import listing_intent
import nvidia_api
from openrouter_provider import GUIDE_MODEL, MODEL, ModelUnavailable, NvidiaAnswerProvider, OpenRouterAnswerProvider
from property_search import search

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
LOGGER = logging.getLogger("uvicorn.error.darglobal")
load_dotenv(ROOT / ".env", interpolate=False)
CHAT_MODE = os.getenv("CHAT_MODE", "mock").strip().lower()
if CHAT_MODE not in {"mock", "live"}:
    raise RuntimeError("CHAT_MODE must be mock or live.")
DATA_MODE = os.getenv("PROPERTY_DATA_MODE", "fixture").strip().lower()
if DATA_MODE not in {"fixture", "live"}:
    raise RuntimeError("PROPERTY_DATA_MODE must be fixture or live.")
RETRIEVAL_MODE = os.getenv("PROPERTY_RETRIEVAL_MODE", "jsonl").strip().lower()
if RETRIEVAL_MODE not in {"jsonl", "hybrid", "nvidia"}:
    raise RuntimeError("PROPERTY_RETRIEVAL_MODE must be jsonl, hybrid, or nvidia.")
CORPUS_VERSION = os.getenv("PROPERTY_CORPUS_VERSION", "v1").strip().lower()
if CORPUS_VERSION not in RELEASES:
    raise RuntimeError("PROPERTY_CORPUS_VERSION must be v1 or v2.")
if DATA_MODE == "live" and CORPUS_VERSION == "v2" and RETRIEVAL_MODE == "hybrid":
    raise RuntimeError("The v2 corpus requires jsonl or nvidia retrieval.")
corpus_file = live_snapshot(CORPUS_VERSION) if DATA_MODE == "live" else None
CORPUS_BACKEND = os.getenv("PROPERTY_CORPUS_BACKEND", "jsonl").strip().lower()
if CORPUS_BACKEND not in {"jsonl", "postgres"}:
    raise RuntimeError("PROPERTY_CORPUS_BACKEND must be jsonl or postgres.")
if CORPUS_BACKEND == "postgres" and DATA_MODE != "live":
    raise RuntimeError("The Postgres corpus requires PROPERTY_DATA_MODE=live.")
CHAT_PROVIDER = os.getenv("CHAT_PROVIDER", "openrouter").strip().lower()
if CHAT_PROVIDER not in {"openrouter", "nvidia"}:
    raise RuntimeError("CHAT_PROVIDER must be openrouter or nvidia.")
DEPLOY_COMMIT = os.getenv("RENDER_GIT_COMMIT", "")
LISTING_RELEASE = os.getenv("PROPERTY_LISTING_RELEASE", "").strip()


def required_setting(name):
    value = os.environ.get(name, "").strip()
    if not value or value == "your_openrouter_api_key_here":
        raise RuntimeError(f"Set {name} before enabling this mode.")
    return value


postgres_corpus = None
if CORPUS_BACKEND == "postgres":
    from corpus_postgres import PostgresCorpus

    postgres_corpus = PostgresCorpus(required_setting("DATABASE_URL"), corpus_file)

vector_lookup = None
nvidia_retrieval = None
if RETRIEVAL_MODE in {"hybrid", "nvidia"} and DATA_MODE == "live":
    from hybrid_retrieval import HybridLookup

    structured_backend = "Postgres" if postgres_corpus is not None else "JSONL"
    if RETRIEVAL_MODE == "nvidia":
        nvidia_retrieval = nvidia_api.NvidiaRetrievalAPI(
            required_setting("Nvidia_Embed"), required_setting("Nvidia_rerank")
        )
        vector_lookup = HybridLookup(
            collection=nvidia_api.collection_for_release(CORPUS_VERSION),
            limit=nvidia_api.RERANK_LIMIT, embedder=nvidia_retrieval,
            vector_config=nvidia_api.vector_config_for_release(CORPUS_VERSION, corpus_file),
            cooldown=0,
            structured_backend=structured_backend,
        )
    else:
        vector_lookup = HybridLookup(structured_backend=structured_backend)

answer_provider = None
if CHAT_MODE == "live":
    if CHAT_PROVIDER == "nvidia":
        answer_provider = NvidiaAnswerProvider(required_setting("Nvidia_chat"))
    else:
        answer_provider = OpenRouterAnswerProvider(required_setting("OPENROUTER_API_KEY"))

listing_search = None
if LISTING_RELEASE:
    if postgres_corpus is None or nvidia_retrieval is None:
        raise RuntimeError("PROPERTY_LISTING_RELEASE requires live data, Postgres and NVIDIA retrieval.")
    from listing_store import PostgresListings

    listing_search = PostgresListings(required_setting("DATABASE_URL"), LISTING_RELEASE, nvidia_retrieval)


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        if vector_lookup is not None:
            await run_in_threadpool(vector_lookup.close)
        if nvidia_retrieval is not None:
            await run_in_threadpool(nvidia_retrieval.close)


app = FastAPI(title="Property Research Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/health")
async def health(response: Response):
    if re.fullmatch(r"[0-9a-f]{40}", DEPLOY_COMMIT):
        response.headers["X-Deploy-Commit"] = DEPLOY_COMMIT
    response.headers["X-Retrieval-Mode"] = RETRIEVAL_MODE if vector_lookup is not None else "jsonl"
    response.headers["X-Corpus-Version"] = CORPUS_VERSION if DATA_MODE == "live" else "fixture"
    response.headers["X-Corpus-Backend"] = CORPUS_BACKEND
    if listing_search is not None:
        response.headers["X-Listing-Release"] = LISTING_RELEASE
    return {"status": "ok"}


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@app.get("/", include_in_schema=False)
async def home():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if CHAT_MODE == "live":
        html = html.replace('data-mode="mock"', 'data-mode="live"')
    badge = {"nvidia": "NVIDIA", "openrouter": "OpenRouter"}[CHAT_PROVIDER] if answer_provider else "Preview"
    data_label = "Real corpus" if DATA_MODE == "live" else "Sample data"
    html = html.replace("Preview: Real corpus", f"{badge}: {data_label}")
    if DATA_MODE == "live":
        html = html.replace('data-data-mode="fixture"', 'data-data-mode="live"')
    else:
        html = html.replace("Research DarGlobal projects and Wasalt buyer guides. Project status is dated to the source.",
                            "Try the assistant with fictional records.")
        html = html.replace("Compare Tierra Viva and Les Vagues across location and property type.",
                            "What is the status of Sample Harbour Residences?")
        html = html.replace("What does Wasalt's buyer guide suggest checking before making an offer?",
                            "Show sample Wasalt listings.")
        html = html.replace("Summarize AIDA and add Wasalt's guidance on off-plan risk.",
                            "Show Sample Harbour Residences alongside sample Wasalt listings.")
        html = html.replace("Project comparison", "Project").replace("Buyer guide", "Sample listings")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/api/chat", response_model=ChatReply)
async def chat(
    request: ChatRequest,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    request_id = uuid4().hex
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Corpus-Backend"] = CORPUS_BACKEND
    try:
        if listing_search is not None and listing_intent(request.message):
            response.headers["X-Listing-Release"] = LISTING_RELEASE
            result = await run_in_threadpool(listing_search, request.message)
        else:
            result = await run_in_threadpool(
                search, request.message, snapshot=corpus_file, live_data=DATA_MODE == "live", vector_lookup=vector_lookup,
                rerank=nvidia_retrieval.rerank if nvidia_retrieval is not None else None, record_loader=postgres_corpus,
            )
    except CorpusUnavailable as error:
        LOGGER.warning("chat request_id=%s corpus_backend=postgres error_category=%s", request_id, error.category)
        raise HTTPException(
            status_code=503, detail="The property database is temporarily unavailable.",
            headers={"Cache-Control": "no-store", "X-Request-ID": request_id, "X-Corpus-Backend": "postgres"},
        ) from None
    inference_model = None
    inference_provider = None
    error_category = None
    retrieval_path = result.retrieval_path
    if postgres_corpus is not None:
        retrieval_path = retrieval_path.replace("jsonl", "postgres")
    answer = "\n\n".join([result.message, *result.facts])
    answer_path = "source_summary"
    if result.abstain:
        answer_path = "abstention"
    elif not result.facts:
        answer_path = "no_evidence"
    elif answer_provider is not None and result.rankable and result.evidence:
        try:
            model = GUIDE_MODEL if result.has_guide else MODEL
            if CHAT_PROVIDER == "nvidia":
                model = nvidia_api.CHAT_MODEL
            generated = await answer_provider.generate(request.message, result.evidence, model=model)
            answer = "\n\n".join([generated.text, *result.required_notes])
            inference_model = generated.model
            inference_provider = generated.provider
            answer_path = generated.provider
        except ModelUnavailable as error:
            error_category = error.category
            answer = "Source summary (model answer unavailable).\n\n" + answer
    LOGGER.info(
        "chat request_id=%s corpus_backend=%s answer_path=%s retrieval_path=%s inference_model=%s error_category=%s",
        request_id, CORPUS_BACKEND, answer_path, retrieval_path, inference_model or "-", error_category or "-",
    )
    return ChatReply(
        answer=answer,
        sources=[*result.sources, *result.supporting_sources],
        mode=CHAT_MODE,
        request_id=request_id,
        answer_path=answer_path,
        retrieval_path=retrieval_path,
        inference_model=inference_model,
        inference_provider=inference_provider,
    )
