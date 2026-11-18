import os
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from answers import ChatReply
from openrouter_provider import ModelUnavailable, OpenRouterAnswerProvider
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

ranker = None
if CHAT_MODE == "live":
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your_openrouter_api_key_here":
        raise RuntimeError("Set OPENROUTER_API_KEY in .env before enabling live mode.")
    ranker = OpenRouterAnswerProvider(key)

app = FastAPI(title="Property Research Assistant")
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
    data_label = "Browser-saved data" if DATA_MODE == "live" else "Sample data"
    html = html.replace("Preview · Sample data", f"{badge} · {data_label}")
    if DATA_MODE == "live":
        html = html.replace('data-data-mode="fixture"', 'data-data-mode="live"')
        html = html.replace("What is the status of Sample Harbour Residences?", "Where is AIDA?")
        html = html.replace("Show Sample Harbour Residences alongside Wasalt listings under SAR 1 million.",
                            "Show AIDA alongside Wasalt listings in Riyadh under SAR 1 million.")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/api/chat", response_model=ChatReply)
async def chat(
    request: ChatRequest,
    response: Response,
):
    response.headers["Cache-Control"] = "no-store"
    result = search(request.message, live_data=DATA_MODE == "live")
    try:
        order = (
            await ranker.rank(request.message, result.facts)
            if ranker is not None and result.facts
            else list(range(len(result.facts)))
        )
    except ModelUnavailable as error:
        detail = "The model is temporarily unavailable. Please retry."
        if error.upstream_status is not None:
            detail = f"OpenRouter could not answer (HTTP {error.upstream_status})."
        raise HTTPException(status_code=502, detail=detail) from None
    return ChatReply(
        answer="\n\n".join([result.message, *(result.facts[index] for index in order)]),
        sources=[result.sources[index] for index in order],
        mode=CHAT_MODE,
    )
