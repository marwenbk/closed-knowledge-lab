from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Protocol, TypeVar

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import Settings


class LLMError(RuntimeError):
    pass


ResponseT = TypeVar("ResponseT", bound=BaseModel)


class LLMProvider(Protocol):
    provider_id: str
    model_id: str

    @property
    def model_version(self) -> str: ...

    def ensure_ready(self) -> str: ...

    def structured_generate(
        self,
        messages: Sequence[Mapping[str, str]],
        response_model: type[ResponseT],
    ) -> ResponseT: ...

    def close(self) -> None: ...


class _Model(BaseModel):
    id: str


class _ModelList(BaseModel):
    data: list[_Model]


class _ChatMessage(BaseModel):
    content: str | None


class _ChatChoice(BaseModel):
    finish_reason: str
    message: _ChatMessage


class _ChatResponse(BaseModel):
    model: str
    system_fingerprint: str | None = None
    choices: list[_ChatChoice] = Field(min_length=1)


class DeepSeekProvider:
    provider_id = "deepseek"

    def __init__(self, settings: Settings, client: httpx.Client | None = None) -> None:
        secret = settings.deepseek_api_key
        api_key = secret.get_secret_value().strip() if secret is not None else ""
        if not api_key:
            raise LLMError("DEEPSEEK_API_KEY is not configured")
        self.model_id: str = settings.chat_model
        self._settings = settings
        self._client = client or httpx.Client(
            base_url=settings.deepseek_base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.llm_timeout_seconds,
            trust_env=False,
        )
        self._owns_client = client is None
        self._model_version: str | None = None

    @property
    def model_version(self) -> str:
        if self._model_version is None:
            raise LLMError("The configured DeepSeek model has not been verified")
        return self._model_version

    def ensure_ready(self) -> str:
        try:
            response = self._client.get("/models")
            response.raise_for_status()
            models = _ModelList.model_validate(response.json()).data
        except (httpx.HTTPError, ValueError, ValidationError) as exc:
            raise LLMError("The DeepSeek API is unavailable or rejected the credentials") from exc
        if self.model_id not in {model.id for model in models}:
            raise LLMError(f"The configured DeepSeek model is unavailable: {self.model_id}")
        self._model_version = self.model_id
        return self.model_id

    def structured_generate(
        self,
        messages: Sequence[Mapping[str, str]],
        response_model: type[ResponseT],
    ) -> ResponseT:
        if not messages:
            raise LLMError("Structured generation requires at least one message")
        if self._model_version is None:
            self.ensure_ready()
        schema = json.dumps(response_model.model_json_schema(), ensure_ascii=False)
        prepared_messages = [dict(message) for message in messages]
        prepared_messages[0]["content"] += (
            " Responda somente com um objeto JSON válido que corresponda exatamente a este "
            f"JSON Schema: {schema}"
        )
        for attempt in range(2):
            try:
                response = self._client.post(
                    "/chat/completions",
                    json={
                        "model": self.model_id,
                        "messages": prepared_messages,
                        "stream": False,
                        "thinking": {"type": "disabled"},
                        "response_format": {"type": "json_object"},
                        "max_tokens": self._settings.llm_max_output_tokens,
                        "temperature": self._settings.llm_temperature,
                    },
                )
                response.raise_for_status()
                completion = _ChatResponse.model_validate(response.json())
                choice = completion.choices[0]
                if choice.finish_reason != "stop":
                    raise LLMError("The DeepSeek response was incomplete")
                if not choice.message.content:
                    raise ValueError("DeepSeek returned empty JSON content")
                result = response_model.model_validate_json(choice.message.content)
            except LLMError:
                raise
            except httpx.HTTPError as exc:
                raise LLMError("The DeepSeek API request failed") from exc
            except (ValueError, ValidationError) as exc:
                if attempt == 0:
                    prepared_messages[0]["content"] += (
                        " Garanta JSON sintaticamente válido, com strings curtas e aspas escapadas."
                    )
                    continue
                raise LLMError("DeepSeek returned an invalid structured response") from exc
            self._model_version = completion.system_fingerprint or completion.model
            return result
        raise LLMError("DeepSeek returned an invalid structured response")

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
