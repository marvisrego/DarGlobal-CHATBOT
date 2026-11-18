import asyncio

import httpx

from answers import ChatReply

MODEL = "nvidia/nemotron-3.5-lightning:free"
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
INSTRUCTIONS = (
    "You are a property research assistant. Give concise, practical guidance. "
    "You have no live property data or verified source documents. Do not invent listings, "
    "prices, availability, development specifications, or citations. If a question requires "
    "current property data, explain the limit and suggest what the user can verify."
)


class ModelUnavailable(Exception):
    def __init__(self, upstream_status: int | None = None):
        self.upstream_status = upstream_status
        super().__init__("OpenRouter did not return a usable answer")


class OpenRouterAnswerProvider:
    def __init__(self, api_key: str):
        self._api_key = api_key

    async def reply(self, message: str) -> ChatReply:
        try:
            async with asyncio.timeout(40), httpx.AsyncClient(
                timeout=httpx.Timeout(35.0, connect=5.0)
            ) as client:
                response = await client.post(
                    ENDPOINT,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": MODEL,
                        "messages": [
                            {"role": "system", "content": INSTRUCTIONS},
                            {"role": "user", "content": message},
                        ],
                        "max_tokens": 600,
                        "stream": False,
                    },
                )
                response.raise_for_status()
                answer = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as error:
            raise ModelUnavailable(error.response.status_code) from None
        except (httpx.RequestError, TimeoutError, ValueError, KeyError, IndexError, TypeError):
            raise ModelUnavailable() from None

        if not isinstance(answer, str) or not answer.strip():
            raise ModelUnavailable()
        return ChatReply(answer=answer.strip(), sources=[], mode="live")
