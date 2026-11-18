import asyncio
import re
from typing import Literal, Protocol

from pydantic import BaseModel


class Source(BaseModel):
    title: str
    publisher: str
    url: str | None = None


class ChatReply(BaseModel):
    answer: str
    sources: list[Source]
    mode: Literal["mock", "live"]


class AnswerProvider(Protocol):
    async def reply(self, message: str) -> ChatReply: ...


DEVELOPMENT = Source(title="AIDA development profile", publisher="DarGlobal")
LISTINGS = Source(title="Riyadh apartment search", publisher="Wasalt")


class SampleAnswerProvider:
    async def reply(self, message: str) -> ChatReply:
        await asyncio.sleep(0.65)
        query = " ".join(message.casefold().split())
        has_development = "darglobal" in query or bool(re.search(r"\baida\b", query))
        has_listings = "wasalt" in query
        comparison = any(word in query for word in ("compar", "versus", " vs ", "across sources"))

        if comparison and has_development and has_listings:
            answer = (
                "These sample records describe two different buying decisions. The DarGlobal record "
                "describes AIDA at the development level. The Wasalt record describes individual "
                "Riyadh apartments with asking prices.\n\n"
                "For a useful comparison, align the home type and size first, then check total "
                "purchase cost, fees, and handover or move-in timing. The sample records do not "
                "include enough matching details for a price comparison."
            )
            sources = [DEVELOPMENT, LISTINGS]
        elif has_development and not has_listings and not comparison and re.search(r"\baida\b", query):
            answer = (
                "In this preview, AIDA appears as a DarGlobal development in Oman with a sample "
                "villa profile. The fixture describes a coastal setting and shared amenities.\n\n"
                "For a buyer shortlist, ask for the exact home type, floor plan, payment schedule, "
                "and expected handover. Those details are outside this sample."
            )
            sources = [DEVELOPMENT]
        elif has_listings and not has_development and not comparison and "riyadh" in query and re.search(
            r"(?<![\d.])1(?:\.0+)?\s*(?:million\b|m\b)|(?<!\d)1[ ,]?000[ ,]?000(?!\d)", query
        ):
            answer = (
                "The sample Wasalt search shows two Riyadh apartments below SAR 1 million.\n\n"
                "Al Narjis — SAR 875,000, two bedrooms, 112 m².\n"
                "Al Malqa — SAR 965,000, three bedrooms, 126 m².\n\n"
                "Both entries and asking prices are synthetic. Use them to see how a budget search "
                "would present matches and cite its source."
            )
            sources = [LISTINGS]
        else:
            answer = (
                "This preview can demonstrate three sample searches: a DarGlobal overview of AIDA, "
                "Wasalt apartments in Riyadh under SAR 1 million, and a comparison of DarGlobal "
                "and Wasalt records. Try one of those questions to see a sample answer and its sources."
            )
            sources = []

        return ChatReply(answer=answer, sources=sources, mode="mock")
