"""Tests for DeepSeek/Qwen provider-aware structured model requests."""

from pathlib import Path
import sys

import pytest

CODE_DIR = Path(__file__).resolve().parents[3] / "code"
if str(CODE_DIR) not in sys.path:
    sys.path.insert(0, str(CODE_DIR))

from structured_data import qwen_client


class FakeResponse:
    """Minimal response object for client tests."""

    def __init__(self, status_code: int, body: dict, text: str = "") -> None:
        """构造测试响应；参数 status_code/body/text 为响应内容。"""

        self.status_code = status_code
        self._body = body
        self.text = text or str(body)

    def json(self) -> dict:
        """返回 JSON 数据；参数为空。"""

        return self._body


@pytest.fixture(autouse=True)
def clear_provider_circuits():
    """在每个测试前后清空供应商熔断；参数为空。"""

    with qwen_client._PROVIDER_CIRCUIT_LOCK:
        qwen_client._PROVIDER_CIRCUITS.clear()
    with qwen_client._RATE_LIMIT_LOCK:
        qwen_client._RATE_LIMIT_STATE["limit"] = (
            qwen_client.DEFAULT_MODEL_CONCURRENCY
        )
        qwen_client._RATE_LIMIT_STATE["active"] = 0
    yield
    with qwen_client._PROVIDER_CIRCUIT_LOCK:
        qwen_client._PROVIDER_CIRCUITS.clear()
    with qwen_client._RATE_LIMIT_LOCK:
        qwen_client._RATE_LIMIT_STATE["limit"] = (
            qwen_client.DEFAULT_MODEL_CONCURRENCY
        )
        qwen_client._RATE_LIMIT_STATE["active"] = 0


def test_deepseek_chat_uses_flash_protocol_and_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek Flash 应使用 thinking 参数并支持 JSON 输出。"""

    client = qwen_client.StructuredModelClient(
        api_key="deepseek-key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
    )
    captured: dict = {}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        """捕获请求；参数 url/kwargs 为请求配置。"""

        captured.update({"url": url, **kwargs})
        return FakeResponse(
            200,
            {
                "choices": [{"message": {"content": '{"status":"ok"}'}}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            },
        )

    monkeypatch.setattr(client.session, "post", fake_post)
    result = client.chat(
        model="deepseek-flash",
        messages=[{"role": "user", "content": "抽取基金指标"}],
        enable_thinking=False,
    )

    payload = qwen_client.json.loads(captured["data"])
    assert result == '{"status":"ok"}'
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert payload["response_format"] == {"type": "json_object"}
    assert payload["thinking"] == {"type": "disabled"}
    assert "enable_thinking" not in payload


def test_local_ollama_chat_uses_native_api_and_disables_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """本地 Ollama 应使用原生接口，并显式关闭 Qwen3 思考输出。"""

    client = qwen_client.StructuredModelClient(
        base_url="http://127.0.0.1:11434/v1",
        provider="local",
    )
    captured: dict = {}

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        """捕获本地请求；参数 url/kwargs 为请求配置。"""

        captured.update({"url": url, **kwargs})
        return FakeResponse(
            200,
            {
                "message": {"role": "assistant", "content": '{"status":"ok"}'},
                "prompt_eval_count": 12,
                "eval_count": 4,
            },
        )

    monkeypatch.setattr(client.session, "post", fake_post)
    result = client.chat(
        model="qwen3:4b",
        messages=[{"role": "user", "content": "返回 JSON"}],
        enable_thinking=False,
        max_tokens=32,
    )

    payload = qwen_client.json.loads(captured["data"])
    assert result == '{"status":"ok"}'
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert payload["think"] is False
    assert payload["format"] == "json"
    assert payload["options"]["num_predict"] == 32
    assert client.token_usage()["total_tokens"] == 16


def test_local_ollama_repeat_limit_retries_without_large_json_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ollama token-loop errors should get one safer JSON-mode retry."""

    client = qwen_client.StructuredModelClient(
        base_url="http://127.0.0.1:11434/v1",
        provider="local",
    )
    captured: list[dict] = []
    responses = [
        FakeResponse(
            500,
            {"error": "prediction aborted, token repeat limit reached"},
        ),
        FakeResponse(
            200,
            {
                "message": {"role": "assistant", "content": '{"ok":true}'},
                "prompt_eval_count": 4,
                "eval_count": 2,
            },
        ),
    ]

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        """Capture both attempts; parameters url/kwargs are request data."""

        captured.append(qwen_client.json.loads(kwargs["data"]))
        return responses.pop(0)

    monkeypatch.setattr(client.session, "post", fake_post)
    result = client.chat(
        model="qwen3:4b",
        messages=[{"role": "user", "content": "返回 JSON"}],
        response_schema={"type": "object"},
        max_tokens=64,
    )

    assert result == '{"ok":true}'
    assert len(captured) == 2
    assert captured[0]["format"] == "json"
    assert captured[1]["options"]["repeat_penalty"] >= 1.18
    assert captured[1]["options"]["top_p"] == 0.9


def test_provider_uses_only_its_own_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DeepSeek 与 Qwen 必须使用各自独立的密钥和地址。"""

    values = {
        ("processing", "structured_provider"): "deepseek",
        ("api", "deepseek_api_key"): "deepseek-key",
        ("api", "deepseek_base_url"): "https://api.deepseek.com",
        ("api", "qwen_api_key"): "qwen-key",
        ("api", "qwen_base_url"): "https://qwen.example.com/v1",
    }
    monkeypatch.setattr(
        qwen_client,
        "config_value",
        lambda section, option, default="": values.get(
            (section, option),
            default,
        ),
    )
    for name in (
        "QWEN_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "QWEN_BASE_URL",
        "DEEPSEEK_BASE_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    deepseek = qwen_client.StructuredModelClient()
    qwen = qwen_client.StructuredModelClient(provider="qwen")

    assert deepseek.provider == "deepseek"
    assert deepseek.api_key == "deepseek-key"
    assert deepseek.base_url == "https://api.deepseek.com"
    assert qwen.provider == "qwen"
    assert qwen.api_key == "qwen-key"
    assert qwen.base_url == "https://qwen.example.com/v1"


def test_task_provider_environment_overrides_global_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """任务级 provider 环境变量必须覆盖 config.ini 的全局供应商。"""

    monkeypatch.setattr(
        qwen_client,
        "config_value",
        lambda section, option, default="": (
            "deepseek"
            if (section, option) == ("processing", "structured_provider")
            else default
        ),
    )
    monkeypatch.setenv("FACTSHEET_STRUCTURED_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_MODEL_BASE_URL", "http://127.0.0.1:11434/v1")

    client = qwen_client.StructuredModelClient()

    assert client.provider == "local"
    assert client.base_url == "http://127.0.0.1:11434/v1"


def test_account_error_opens_provider_circuit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """账户欠费错误应熔断后续调用，避免整批重复请求。"""

    client = qwen_client.StructuredModelClient(
        api_key="deepseek-key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
    )
    calls = 0

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        """返回欠费错误；参数 url/kwargs 为请求配置。"""

        nonlocal calls
        calls += 1
        return FakeResponse(
            402,
            {"error": {"code": "Insufficient Balance"}},
            '{"error":{"code":"Insufficient Balance"}}',
        )

    monkeypatch.setattr(client.session, "post", fake_post)
    with pytest.raises(qwen_client.QwenClientError, match="HTTP 402"):
        client.chat(
            model="deepseek-flash",
            messages=[{"role": "user", "content": "test"}],
        )
    with pytest.raises(qwen_client.QwenClientError, match="已停止重复请求"):
        client.chat(
            model="deepseek-flash",
            messages=[{"role": "user", "content": "test"}],
        )
    assert calls == 1


def test_rate_limit_errors_use_exponential_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """429 限流应重试并使用递增退避；参数为空。"""

    delays: list[float] = []
    calls = {"count": 0}
    monkeypatch.setattr(qwen_client.time, "sleep", delays.append)

    def flaky() -> str:
        """前两次限流，第三次成功；参数为空。"""

        calls["count"] += 1
        if calls["count"] < 3:
            raise qwen_client.QwenClientError(
                "Deepseek API HTTP 429: Too many requests"
            )
        return "ok"

    assert qwen_client.retry_call(flaky, attempts=3) == "ok"
    assert calls["count"] == 3
    assert len(delays) == 2
    assert delays[1] > delays[0]


def test_concurrency_limit_error_shrinks_local_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """服务端并发上限应收缩本地并发名额，避免持续撞限流。"""

    client = qwen_client.StructuredModelClient(
        api_key="deepseek-key",
        base_url="https://api.deepseek.com",
        provider="deepseek",
    )
    body = (
        '{"error":{"message":"Too many requests. Your current concurrency '
        'is 5, which exceeds your concurrency limit of 5 based on your '
        'remaining balance."}}'
    )

    def fake_post(url: str, **kwargs: object) -> FakeResponse:
        """返回并发上限错误；参数 url/kwargs 为请求配置。"""

        return FakeResponse(429, {"error": {"message": "Too many requests"}}, body)

    monkeypatch.setattr(client.session, "post", fake_post)
    with pytest.raises(qwen_client.QwenClientError, match="HTTP 429"):
        client.chat(
            model="deepseek-flash",
            messages=[{"role": "user", "content": "test"}],
        )

    with qwen_client._RATE_LIMIT_LOCK:
        limit = qwen_client._RATE_LIMIT_STATE["limit"]
        active = qwen_client._RATE_LIMIT_STATE["active"]
    assert 1 <= limit <= 5
    assert active == 0
