import json
import urllib.request
from abc import ABC, abstractmethod

from app.core.config import settings


class LLMProvider(ABC):
    @abstractmethod
    def generate(self, messages: list[dict[str, str]]) -> str | None:
        pass


class RetrievalOnlyProvider(LLMProvider):
    def generate(self, messages: list[dict[str, str]]) -> str | None:
        return None


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    def generate(self, messages: list[dict[str, str]]) -> str | None:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.2,
        }
        body = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/v1/chat/completions",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
        choices = data.get("choices") or []
        if not choices:
            return None
        message = choices[0].get("message") or {}
        return message.get("content")


def get_llm_provider() -> LLMProvider:
    if settings.llm_provider_url:
        return OpenAICompatibleProvider(
            base_url=settings.llm_provider_url,
            model=settings.llm_model_name,
        )
    return RetrievalOnlyProvider()
