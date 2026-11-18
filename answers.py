from typing import Literal

from pydantic import BaseModel


class Source(BaseModel):
    title: str
    publisher: str
    url: str | None = None
    fixture: bool = False


class ChatReply(BaseModel):
    answer: str
    sources: list[Source]
    mode: Literal["mock", "live"]
