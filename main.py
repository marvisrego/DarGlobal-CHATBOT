import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

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

vector_lookup = None
if RETRIEVAL_MODE == "hybrid" and DATA_MODE == "live":
    from hybrid_retrieval import HybridLookup

    vector_lookup = HybridLookup()

ranker = None
if CHAT_MODE == "live":
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your_openrouter_api_key_here":
        raise RuntimeError("Set OPENROUTER_API_KEY in .env before enabling live mode.")
    ranker = OpenRouterAnswerProvider(key)


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
async def health():
    return {"status": "ok"}


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


@app.get("/", include_in_schema=False)
async def home():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if CHAT_MODE == "live":
        html = html.replace('data-mode="mock"', 'data-mode="live"')
    badge = "OpenRouter" if ranker is not None else "Preview"
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
    result = await run_in_threadpool(
        search, request.message, live_data=DATA_MODE == "live", vector_lookup=vector_lookup
    )
    inference_model = None
    order = list(range(len(result.facts)))
    message = result.message
    try:
        if ranker is not None and result.facts and result.rankable:
            model = GUIDE_MODEL if result.has_guide else MODEL
            order = await ranker.rank(request.message, result.facts, model=model)
            inference_model = model
    except ModelUnavailable:
        message += " Model ranking is temporarily unavailable. Showing the retrieved evidence."
    return ChatReply(
        answer="\n\n".join([message, *(result.facts[index] for index in order)]),
        sources=[*(result.sources[index] for index in order), *result.supporting_sources],
        mode=CHAT_MODE,
        inference_model=inference_model,
    )
