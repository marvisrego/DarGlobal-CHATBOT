import os
from pathlib import Path
from typing import Annotated

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from answers import AnswerProvider, ChatReply, SampleAnswerProvider
from openrouter_provider import ModelUnavailable, OpenRouterAnswerProvider

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
load_dotenv(ROOT / ".env", interpolate=False)
CHAT_MODE = os.getenv("CHAT_MODE", "mock").strip().lower()
if CHAT_MODE not in {"mock", "live"}:
    raise RuntimeError("CHAT_MODE must be mock or live.")

provider: AnswerProvider = SampleAnswerProvider()
if CHAT_MODE == "live":
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key or key == "your_openrouter_api_key_here":
        raise RuntimeError("Set OPENROUTER_API_KEY in .env before enabling live mode.")
    provider = OpenRouterAnswerProvider(key)

app = FastAPI(title="Property Research Assistant")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


def get_answer_provider() -> AnswerProvider:
    return provider


@app.get("/", include_in_schema=False)
async def home():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    if CHAT_MODE == "live":
        html = html.replace('data-mode="mock"', 'data-mode="live"')
        html = html.replace("Preview · sample data", "OpenRouter · no live listings")
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@app.post("/api/chat", response_model=ChatReply)
async def chat(
    request: ChatRequest,
    response: Response,
    provider: Annotated[AnswerProvider, Depends(get_answer_provider)],
):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await provider.reply(request.message)
    except ModelUnavailable as error:
        detail = "The model is temporarily unavailable. Please retry."
        if error.upstream_status is not None:
            detail = f"OpenRouter could not answer (HTTP {error.upstream_status})."
        raise HTTPException(status_code=502, detail=detail) from None
