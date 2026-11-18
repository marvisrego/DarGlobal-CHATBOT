import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from uuid import uuid4

from dotenv import load_dotenv
from fastapi import FastAPI, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints
from starlette.concurrency import run_in_threadpool

from answers import ChatReply
from openrouter_provider import GUIDE_MODEL, MODEL, ModelUnavailable, OpenRouterAnswerProvider
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
if RETRIEVAL_MODE not in {"jsonl", "hybrid"}:
    raise RuntimeError("PROPERTY_RETRIEVAL_MODE must be jsonl or hybrid.")
DEPLOY_COMMIT = os.getenv("RENDER_GIT_COMMIT", "")

vector_lookup = None
if RETRIEVAL_MODE == "hybrid" and DATA_MODE == "live":
    from hybrid_retrieval import HybridLookup

    vector_lookup = HybridLookup()

answer_provider = None
if CHAT_MODE == "live":
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your_openrouter_api_key_here":
        raise RuntimeError("Set OPENROUTER_API_KEY in .env before enabling live mode.")
    answer_provider = OpenRouterAnswerProvider(key)


@asynccontextmanager
async def lifespan(_app):
    try:
        yield
    finally:
        if vector_lookup is not None:
            await run_in_threadpool(vector_lookup.close)


app = FastAPI(title="Property Research Assistant", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/health")
async def health(response: Response):
    if re.fullmatch(r"[0-9a-f]{40}", DEPLOY_COMMIT):
        response.headers["X-Deploy-Commit"] = DEPLOY_COMMIT
    response.headers["X-Retrieval-Mode"] = "hybrid" if vector_lookup is not None else "jsonl"
    return {"status": "ok"}


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@app.get("/", include_in_schema=False)
async def home():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if CHAT_MODE == "live":
        html = html.replace('data-mode="mock"', 'data-mode="live"')
    badge = "OpenRouter" if answer_provider is not None else "Preview"
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
    result = await run_in_threadpool(
        search, request.message, live_data=DATA_MODE == "live", vector_lookup=vector_lookup
    )
    inference_model = None
    error_category = None
    answer = "\n\n".join([result.message, *result.facts])
    answer_path = "source_summary"
    if result.abstain:
        answer_path = "abstention"
    elif not result.facts:
        answer_path = "no_evidence"
    elif answer_provider is not None and result.rankable:
        try:
            model = GUIDE_MODEL if result.has_guide else MODEL
            generated = await answer_provider.generate(request.message, result.facts, result.message, model=model)
            answer = generated.text
            inference_model = generated.model
            answer_path = "openrouter"
        except ModelUnavailable as error:
            error_category = error.category
            answer = "Source summary (OpenRouter answer unavailable).\n\n" + answer
    LOGGER.info(
        "chat request_id=%s answer_path=%s retrieval_path=%s inference_model=%s error_category=%s",
        request_id, answer_path, result.retrieval_path, inference_model or "-", error_category or "-",
    )
    return ChatReply(
        answer=answer,
        sources=[*result.sources, *result.supporting_sources],
        mode=CHAT_MODE,
        request_id=request_id,
        answer_path=answer_path,
        retrieval_path=result.retrieval_path,
        inference_model=inference_model,
    )
