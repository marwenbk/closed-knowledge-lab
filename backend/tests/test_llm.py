from __future__ import annotations

import json
import os
from typing import Literal

import httpx
import pytest
from app.answering import answer_knowledge
from app.config import Settings
from app.db import get_engine
from app.embeddings import OnnxE5EmbeddingProvider
from app.evaluation import load_evaluation_data
from app.llm import DeepSeekProvider, LLMError
from pydantic import BaseModel, SecretStr

PIPELINE_CASE_IDS = (
    "direct_family_dependents_001",
    "multi_gold_dependents_001",
    "multi_platinum_psychology_001",
    "partial_price_annual_001",
    "out_of_scope_001",
    "ambiguity_001",
    "injection_no_answer_001",
    "injection_grounded_001",
)


class StructuredReply(BaseModel):
    value: str


class HostedModelProbe(BaseModel):
    status: Literal["ok"]


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        deepseek_base_url="https://api.deepseek.com",
        deepseek_api_key=SecretStr("test-key"),
        chat_model="deepseek-v4-flash",
    )


def test_deepseek_provider_validates_model_and_structured_output() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "deepseek-v4-flash"}]})
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "system_fingerprint": "fp_test",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"value":"ok"}'},
                    }
                ],
            },
        )

    client = httpx.Client(
        base_url="https://api.deepseek.com",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    provider = DeepSeekProvider(_settings(), client)

    result = provider.structured_generate(
        [{"role": "system", "content": "Return structured data."}],
        StructuredReply,
    )

    assert result == StructuredReply(value="ok")
    assert provider.model_version == "fp_test"
    assert requests[0].headers["Authorization"] == "Bearer test-key"
    payload = json.loads(requests[1].content)
    assert payload["model"] == "deepseek-v4-flash"
    assert payload["stream"] is False
    assert payload["thinking"] == {"type": "disabled"}
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["temperature"] == 0
    assert "JSON Schema" in payload["messages"][0]["content"]


def test_deepseek_provider_rejects_an_unavailable_model() -> None:
    client = httpx.Client(
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"data": [{"id": "deepseek-v4-pro"}]})
        ),
    )
    provider = DeepSeekProvider(_settings(), client)

    with pytest.raises(LLMError, match="unavailable"):
        provider.ensure_ready()


def test_deepseek_provider_requires_an_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        DeepSeekProvider(Settings(_env_file=None))


def test_deepseek_provider_retries_invalid_json_once() -> None:
    chat_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chat_calls
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "deepseek-v4-flash"}]})
        chat_calls += 1
        content = "not json" if chat_calls == 1 else '{"value":"ok"}'
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": content}}],
            },
        )

    client = httpx.Client(
        base_url="https://api.deepseek.com",
        transport=httpx.MockTransport(handler),
    )
    provider = DeepSeekProvider(_settings(), client)

    result = provider.structured_generate(
        [{"role": "system", "content": "Answer in JSON."}], StructuredReply
    )

    assert result == StructuredReply(value="ok")
    assert chat_calls == 2


def test_deepseek_provider_fails_closed_after_invalid_json_retry() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": [{"id": "deepseek-v4-flash"}]})
        return httpx.Response(
            200,
            json={
                "model": "deepseek-v4-flash",
                "choices": [{"finish_reason": "stop", "message": {"content": "not json"}}],
            },
        )

    provider = DeepSeekProvider(
        _settings(),
        httpx.Client(
            base_url="https://api.deepseek.com",
            transport=httpx.MockTransport(handler),
        ),
    )

    with pytest.raises(LLMError, match="invalid structured response"):
        provider.structured_generate(
            [{"role": "system", "content": "Answer in JSON."}], StructuredReply
        )


def test_settings_reject_non_official_deepseek_endpoint() -> None:
    with pytest.raises(ValueError, match="official HTTPS"):
        Settings(_env_file=None, deepseek_base_url="https://example.com")


@pytest.mark.llm
def test_deepseek_v4_flash_returns_structured_output() -> None:
    if os.environ.get("TOPMED_REQUIRE_LLM_TESTS") != "1":
        pytest.skip("Live DeepSeek verification was not requested")
    provider = DeepSeekProvider(Settings())
    try:
        result = provider.structured_generate(
            [
                {
                    "role": "system",
                    "content": "Return the requested value as JSON.",
                },
                {"role": "user", "content": "Return status ok."},
            ],
            HostedModelProbe,
        )
    finally:
        provider.close()

    assert result == HostedModelProbe(status="ok")


@pytest.mark.llm
@pytest.mark.postgres
def test_representative_deepseek_pipeline_cases() -> None:
    if os.environ.get("TOPMED_REQUIRE_LLM_TESTS") != "1":
        pytest.skip("Live DeepSeek verification was not requested")
    settings = Settings()
    cases = {case.id: case for case in load_evaluation_data().cases if case.id in PIPELINE_CASE_IDS}
    assert set(cases) == set(PIPELINE_CASE_IDS)

    engine = get_engine()
    embedding = OnnxE5EmbeddingProvider(settings)
    provider = DeepSeekProvider(settings)
    try:
        for case_id in PIPELINE_CASE_IDS:
            case = cases[case_id]
            result = answer_knowledge(
                engine,
                embedding,
                provider,
                settings,
                case.messages[-1],
                conversation_context=case.messages[:-1],
            )
            assert result.status == case.expected_status, case_id
            expected_documents = set(case.expected_documents.canonical)
            cited_documents = {citation.document_key for citation in result.citations}
            assert expected_documents.issubset(cited_documents), case_id
            if result.status in {"ANSWERABLE", "PARTIALLY_ANSWERABLE"}:
                assert result.verification_status == "VERIFIED", case_id
                assert result.citations, case_id
            else:
                assert result.verification_status == "NOT_REQUIRED", case_id
                assert not result.citations, case_id
    finally:
        provider.close()
        engine.dispose()
