from dataclasses import dataclass
from typing import Protocol

from openai import AsyncOpenAI
from openai.types.responses import ResponseTextConfigParam

from .schemas import Selection, Settings


@dataclass
class Reply:
    text: str
    input_tokens: int | None
    output_tokens: int | None
    status: str = "completed"


class Provider(Protocol):
    async def complete(self, instructions: str, payload: str) -> Reply: ...


class OpenAIProvider:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = AsyncOpenAI(timeout=settings.timeout, max_retries=0)

    async def complete(self, instructions: str, payload: str) -> Reply:
        text_config: ResponseTextConfigParam = {
            "format": {
                "type": "json_schema",
                "name": "product_matches",
                "strict": True,
                "schema": Selection.model_json_schema(),
            }
        }
        response = await self.client.responses.create(
            model=self.settings.model,
            instructions=instructions,
            input=payload,
            max_output_tokens=self.settings.max_output_tokens,
            store=False,
            text=text_config,
        )
        usage = response.usage
        return Reply(
            text=response.output_text,
            input_tokens=usage.input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            status=response.status or "unknown",
        )

    async def close(self) -> None:
        await self.client.close()
