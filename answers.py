from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

OMRAN_AIDA_URL = (
    "https://omran.om/index.php/news/2022/"
    "omran-group-and-dar-al-arkan-announce-partnership-to-develop-aida-in-yiti"
)
OMRAN_AIDA_METHOD = "httpx_partner_announcement"
DAR_REPORT_URL = "https://data.fca.org.uk/artefacts/NSM/DirectUpload/NI-000152113/NI-000152113.pdf"
DAR_REPORT_CITATION = (
    "https://cdn.darglobal.co.uk/Dar%20Global%20-%20HY%202026%20Results%20Presentation%20-%20Final.pdf#page=24"
)
DAR_REPORT_METHOD = "httpx_regulatory_pdf"
DAR_WEB_METHOD = "browser_visible_project_summary"
WASALT_GUIDE_URL = "https://blog.wasalt.sa/en/how-to-buy-a-house-in-saudi-arabia/"
WASALT_GUIDE_METHOD = "browser_visible_guide_summary"


class Source(BaseModel):
    title: str
    publisher: str
    url: str | None = None
    fixture: bool = False


@dataclass(frozen=True)
class Evidence:
    title: str
    publisher: str
    claims: tuple[str, ...]
    is_guide: bool = False


class ChatReply(BaseModel):
    answer: str
    sources: list[Source]
    mode: Literal["mock", "live"]
    request_id: str
    answer_path: Literal["openrouter", "nvidia", "source_summary", "no_evidence", "abstention"]
    retrieval_path: Literal["jsonl", "postgres", "qdrant", "jsonl_after_qdrant_failure", "postgres_after_qdrant_failure"]
    inference_model: str | None = None
    inference_provider: Literal["openrouter", "nvidia"] | None = None
