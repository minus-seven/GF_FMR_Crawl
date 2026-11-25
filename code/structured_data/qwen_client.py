#!/usr/bin/env python
"""OpenAI-compatible clients for Qwen and DeepSeek structured extraction."""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlparse
from pathlib import Path
from typing import Any

import requests

try:
    from app_config import value as config_value
except ImportError:  # pragma: no cover - standalone legacy invocation
    config_value = None


DEFAULT_QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_LOCAL_MODEL_BASE_URL = "http://127.0.0.1:11434/v1"
STRUCTURED_SYSTEM_PROMPT = """
你是基金月报结构化抽取模块。只依据用户提供的 Markdown、PDF 文本或图片作答。
不得猜测未披露字段；名称、币种、单位、报告期和证券代码必须来自输入。
区分百分比配置、金额、年限、收益率和信用评级口径，不得跨字段复用数字。
列表分布需要保留原文名称并逐项配对；证据不足时返回 null 或空数组。
所有输出必须是合法 JSON，不得输出解释、Markdown 围栏或 Markdown 代码块。
""".strip()
_PROVIDER_CIRCUIT_LOCK = threading.Lock()
_PROVIDER_CIRCUITS: dict[str, str] = {}
_RATE_LIMIT_LOCK = threading.Condition()
DEFAULT_MODEL_CONCURRENCY = max(
    1,
    int(os.environ.get("FACTSHEET_MODEL_CONCURRENCY", "16") or 16),
)
_RATE_LIMIT_STATE = {
    "limit": DEFAULT_MODEL_CONCURRENCY,
    "active": 0,
}
_CONCURRENCY_LIMIT_PATTERN = re.compile(
    r"concurrency limit of (\d+)",
    re.IGNORECASE,
)
RATE_LIMIT_MAX_ATTEMPTS = 6
RATE_LIMIT_BASE_DELAY_SECONDS = 4.0
RATE_LIMIT_MAX_DELAY_SECONDS = 60.0

try:
    from . import local_config
except ImportError:  # pragma: no cover - local_config.py is optional and gitignored
    local_config = None
except SyntaxError as exc:  # pragma: no cover - depends on local-only config edits
    raise RuntimeError("code/structured_data/local_config.py has invalid Python syntax. Check quotes around QWEN_API_KEY.") from exc


class QwenClientError(RuntimeError):
    """Raised when a structured-model API request or response is unusable."""


PROVIDER_UNAVAILABLE_MARKERS = (
    "insufficient balance",
    "arrearage",
    "account is in good standing",
    "已停止重复请求",
    "http 401",
    "http 402",
    "http 403",
)


def provider_unavailable_reason(message: str) -> str | None:
    """识别账户级不可用错误；参数 message 为异常或失败描述。

    返回命中的标记，未命中时返回 None，供批处理任务快速熔断。
    """

    normalized = str(message or "").casefold()
    if not normalized:
        return None
    for marker in PROVIDER_UNAVAILABLE_MARKERS:
        if marker in normalized:
            return marker
    return None


def is_rate_limit_error(message: str) -> bool:
    """判断错误是否为可重试的接口限流；参数 message 为异常描述。"""

    normalized = str(message or "").casefold()
    if not normalized:
        return False
    return (
        "http 429" in normalized
        or "rate_limit" in normalized
        or "too many requests" in normalized
        or "concurrency limit" in normalized
    )


def _acquire_model_slot() -> None:
    """获取一个模型请求并发名额；参数为空。"""

    with _RATE_LIMIT_LOCK:
        while _RATE_LIMIT_STATE["active"] >= _RATE_LIMIT_STATE["limit"]:
            _RATE_LIMIT_LOCK.wait()
        _RATE_LIMIT_STATE["active"] += 1


def _release_model_slot() -> None:
    """释放模型请求并发名额；参数为空。"""

    with _RATE_LIMIT_LOCK:
        _RATE_LIMIT_STATE["active"] = max(
            0,
            _RATE_LIMIT_STATE["active"] - 1,
        )
        _RATE_LIMIT_LOCK.notify()


def _note_concurrency_limit(body: str) -> None:
    """按服务端返回的并发上限收缩本地名额；参数 body 为响应正文。"""

    match = _CONCURRENCY_LIMIT_PATTERN.search(str(body or ""))
    server_limit = int(match.group(1)) if match else None
    with _RATE_LIMIT_LOCK:
        current = _RATE_LIMIT_STATE["limit"]
        target = min(current - 1, (server_limit or current) - 1)
        _RATE_LIMIT_STATE["limit"] = max(1, target)


class StructuredModelClient:
    """OpenAI-compatible client with provider-aware credentials and thinking."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: int = 180,
        provider: str | None = None,
    ) -> None:
        """初始化结构化模型客户端；参数 api_key/base_url/timeout/provider 为连接配置。"""

        # 任务级路由通过环境变量注入子进程；它必须覆盖全局 config.ini，
        # 否则选择本地模型的任务仍会被全局 DeepSeek 配置劫持。
        configured_provider = (
            str(os.environ.get("FACTSHEET_STRUCTURED_PROVIDER") or "").strip()
            or (
                config_value(
                    "processing",
                    "structured_provider",
                    "qwen",
                ) if config_value else "qwen"
            )
        )
        selected_provider = str(provider or configured_provider or "qwen").strip().lower()
        if selected_provider not in {"qwen", "deepseek", "local"}:
            selected_provider = "qwen"
        self.provider = selected_provider
        local_api_key = getattr(local_config, "QWEN_API_KEY", "") if local_config else ""
        local_base_url = getattr(local_config, "QWEN_BASE_URL", "") if local_config else ""
        unified_api_key = config_value("api", "qwen_api_key", "") if config_value else ""
        unified_base_url = config_value("api", "qwen_base_url", "") if config_value else ""
        deepseek_api_key = (
            config_value("api", "deepseek_api_key", "")
            if config_value else ""
        )
        deepseek_base_url = (
            config_value("api", "deepseek_base_url", "")
            if config_value else ""
        )
        configured_local_base_url = (
            config_value("api", "local_model_base_url", "")
            if config_value else ""
        )
        if self.provider == "deepseek":
            self.api_key = (
                api_key
                or os.environ.get("DEEPSEEK_API_KEY")
                or deepseek_api_key
            )
            self.base_url = (
                base_url
                or os.environ.get("DEEPSEEK_BASE_URL")
                or deepseek_base_url
                or DEFAULT_DEEPSEEK_BASE_URL
            )
        elif self.provider == "qwen":
            self.api_key = (
                api_key
                or os.environ.get("QWEN_API_KEY")
                or os.environ.get("DASHSCOPE_API_KEY")
                or unified_api_key
                or local_api_key
            )
            self.base_url = (
                base_url
                or os.environ.get("QWEN_BASE_URL")
                or unified_base_url
                or local_base_url
                or DEFAULT_QWEN_BASE_URL
            )
        else:
            self.api_key = ""
            self.base_url = (
                base_url
                or os.environ.get("LOCAL_MODEL_BASE_URL")
                or configured_local_base_url
                or DEFAULT_LOCAL_MODEL_BASE_URL
            )
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = os.environ.get("QWEN_TRUST_ENV", "").strip() in {"1", "true", "TRUE", "yes", "YES"}
        self.usage_calls: list[dict[str, Any]] = []
        if self.provider != "local" and not self.api_key:
            key_name = "DEEPSEEK_API_KEY" if self.provider == "deepseek" else "QWEN_API_KEY"
            raise QwenClientError(f"缺少 {key_name}，请在模型设置页填写")

    @property
    def api_root(self) -> str:
        root = self.base_url.rstrip("/")
        if root.endswith("/chat/completions"):
            return root[: -len("/chat/completions")]
        return root

    @property
    def chat_url(self) -> str:
        url = self.base_url.rstrip("/")
        if url.endswith("/chat/completions"):
            return url
        return f"{url}/chat/completions"

    def _uses_ollama_native_api(self) -> bool:
        """判断本地地址是否为 Ollama 原生 API；参数为空。"""

        parsed = urlparse(self.base_url)
        return (
            self.provider == "local"
            and parsed.hostname in {"127.0.0.1", "localhost", "::1"}
            and parsed.port == 11434
        )

    def _ollama_chat_url(self) -> str:
        """构造 Ollama 原生聊天地址；参数为空。"""

        parsed = urlparse(self.base_url)
        netloc = parsed.netloc or "127.0.0.1:11434"
        scheme = parsed.scheme or "http"
        return f"{scheme}://{netloc}/api/chat"

    @property
    def responses_url(self) -> str:
        return f"{self.api_root}/responses"

    def upload_file(self, file_path: Path, *, purpose: str = "file-extract") -> str:
        if self.provider == "local":
            raise QwenClientError("本地 Qwen 4B 仅支持 MinerU Markdown 上下文，不支持 PDF 文件上传")
        with file_path.open("rb") as handle:
            response = self._post(
                f"{self.api_root}/files",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": (file_path.name, handle, "application/pdf")},
                data={"purpose": purpose},
                timeout=self.timeout,
            )
        if response.status_code >= 400:
            self._record_provider_error(response.status_code, response.text)
            raise QwenClientError(
                f"{self.provider.title()} file upload HTTP "
                f"{response.status_code}: {response.text[:800]}"
            )
        data = response.json()
        file_id = data.get("id")
        if not file_id:
            raise QwenClientError(
                f"Unexpected {self.provider.title()} file upload response: {data}"
            )
        return str(file_id)

    def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.0,
        enable_thinking: bool = False,
        thinking_budget: int | None = None,
        enable_search: bool = False,
        search_options: dict[str, Any] | None = None,
        structured_output: bool = True,
        response_schema: dict[str, Any] | None = None,
        max_tokens: int | None = None,
        call_name: str = "",
    ) -> str:
        """调用结构化 Chat Completions；参数 model/messages 等为请求配置。"""

        self._raise_if_provider_unavailable()
        if self._uses_ollama_native_api():
            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": False,
                "think": bool(enable_thinking),
                # Qwen3 occasionally falls into a token loop when Ollama's
                # grammar-constrained JSON decoder is enabled.  A modest
                # repeat penalty is safe for deterministic extraction and
                # keeps one malformed field from exhausting the whole task.
                "options": {
                    "temperature": temperature,
                    "repeat_penalty": float(
                        os.environ.get("FACTSHEET_OLLAMA_REPEAT_PENALTY", "1.12")
                    ),
                    "repeat_last_n": int(
                        os.environ.get("FACTSHEET_OLLAMA_REPEAT_LAST_N", "128")
                    ),
                },
            }
            if structured_output:
                # Ollama 0.40.x + Qwen3 can return HTTP 500
                # ``token repeat limit reached`` for the large JSON schema
                # used by the cloud providers.  JSON mode still constrains
                # the response to a single object; evidence/schema checks
                # remain enforced by the existing parser and validator.
                use_schema = os.environ.get(
                    "FACTSHEET_OLLAMA_USE_JSON_SCHEMA", "0"
                ).strip().casefold() in {"1", "true", "yes", "on"}
                payload["format"] = (
                    response_schema if use_schema and response_schema else "json"
                )
            if max_tokens is not None:
                payload["options"]["num_predict"] = max_tokens
            request_url = self._ollama_chat_url()
            request_kwargs = {
                "headers": self._request_headers(),
                "data": json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                "timeout": self.timeout,
            }
            response = self._post(request_url, **request_kwargs)
            # Some Ollama builds still ignore JSON mode for a particular
            # prompt and abort with the repeat-limit guard. Retry once with a
            # slightly stronger penalty and a short nucleus sample rather
            # than propagating a batch-fatal error immediately.
            if (
                response.status_code >= 500
                and "token repeat limit reached" in response.text.casefold()
            ):
                retry_payload = dict(payload)
                retry_options = dict(payload.get("options") or {})
                retry_options.update(
                    {
                        "repeat_penalty": max(
                            float(retry_options.get("repeat_penalty", 1.12)),
                            1.18,
                        ),
                        "top_p": 0.9,
                        "top_k": 40,
                    }
                )
                retry_payload["options"] = retry_options
                response = self._post(
                    request_url,
                    headers=self._request_headers(),
                    data=json.dumps(retry_payload, ensure_ascii=False).encode("utf-8"),
                    timeout=self.timeout,
                )
            if response.status_code >= 400:
                self._record_provider_error(response.status_code, response.text)
                raise QwenClientError(
                    f"{self.provider.title()} API HTTP "
                    f"{response.status_code}: {response.text[:800]}"
                )
            data = response.json()
            usage = data.get("usage")
            if not isinstance(usage, dict):
                usage = {
                    "prompt_tokens": data.get("prompt_eval_count", 0),
                    "completion_tokens": data.get("eval_count", 0),
                    "total_tokens": (
                        int(data.get("prompt_eval_count", 0) or 0)
                        + int(data.get("eval_count", 0) or 0)
                    ),
                }
            self._record_usage(model=model, call_name=call_name, usage=usage)
            try:
                return data["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise QwenClientError(
                    f"{self.provider.title()} API 响应格式异常: {data}"
                ) from exc
        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if structured_output:
            if response_schema and self.provider == "qwen":
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": call_name or "structured_output",
                        "strict": True,
                        "schema": response_schema,
                    },
                }
            else:
                payload["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if self.provider == "deepseek":
            payload["thinking"] = {
                "type": "enabled" if enable_thinking else "disabled"
            }
            if enable_thinking:
                payload["reasoning_effort"] = (
                    "high" if thinking_budget and thinking_budget >= 8192 else "low"
                )
        elif self.provider == "qwen":
            payload["enable_thinking"] = enable_thinking
            if enable_thinking and thinking_budget is not None:
                payload["thinking_budget"] = thinking_budget
        if enable_search:
            if self.provider == "qwen":
                payload["enable_thinking"] = False
                payload["enable_search"] = True
            if search_options:
                payload["search_options"] = search_options
        response = self._post(
            self.chat_url,
            headers=self._request_headers(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            self._record_provider_error(response.status_code, response.text)
            raise QwenClientError(
                f"{self.provider.title()} API HTTP "
                f"{response.status_code}: {response.text[:800]}"
            )
        data = response.json()
        self._record_usage(model=model, call_name=call_name, usage=data.get("usage"))
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
                raise QwenClientError(
                    f"Unexpected {self.provider.title()} response shape: {data}"
                ) from exc

    def _record_usage(self, *, model: str, call_name: str, usage: Any) -> None:
        if not isinstance(usage, dict):
            usage = {}
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        total_tokens = usage.get("total_tokens")
        try:
            input_tokens = int(input_tokens or 0)
            output_tokens = int(output_tokens or 0)
            total_tokens = int(total_tokens if total_tokens is not None else input_tokens + output_tokens)
        except (TypeError, ValueError):
            input_tokens = output_tokens = total_tokens = 0
        self.usage_calls.append({
            "call_name": call_name or "chat",
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_hit_tokens": int(
                usage.get(
                    "prompt_cache_hit_tokens",
                    (usage.get("prompt_tokens_details") or {}).get(
                        "cached_tokens",
                        0,
                    ),
                )
                or 0
            ),
            "cache_miss_tokens": int(
                usage.get(
                    "prompt_cache_miss_tokens",
                    max(
                        input_tokens
                        - int(
                            (usage.get("prompt_tokens_details") or {}).get(
                                "cached_tokens",
                                0,
                            )
                            or 0
                        ),
                        0,
                    ),
                )
                or 0
            ),
            "raw_usage": usage,
        })

    def respond(
        self,
        *,
        model: str,
        input_text: str,
        tools: list[dict[str, Any]] | None = None,
        enable_thinking: bool = True,
        call_name: str = "",
    ) -> str:
        """调用 Responses API；参数 model/input/tools/thinking/call_name 为请求配置。"""

        self._raise_if_provider_unavailable()
        if self.provider == "local":
            if tools:
                raise QwenClientError("本地 Qwen 4B 不支持联网工具调用")
            return self.chat(
                model=model,
                messages=[{"role": "user", "content": input_text}],
                structured_output=False,
                enable_thinking=False,
                call_name=call_name,
            )
        payload: dict[str, Any] = {
            "model": model,
            "input": input_text,
        }
        if self.provider == "deepseek":
            payload["reasoning"] = {
                "effort": "high" if enable_thinking else "none"
            }
        else:
            payload["enable_thinking"] = enable_thinking
        if tools:
            payload["tools"] = tools
        response = self._post(
            self.responses_url,
            headers=self._request_headers(),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            timeout=self.timeout,
        )
        if response.status_code >= 400:
            self._record_provider_error(response.status_code, response.text)
            raise QwenClientError(
                f"{self.provider.title()} Responses API HTTP "
                f"{response.status_code}: {response.text[:800]}"
            )
        data = response.json()
        self._record_usage(model=model, call_name=call_name, usage=data.get("usage"))
        if isinstance(data.get("output_text"), str):
            return data["output_text"]
        text_parts: list[str] = []
        for output_item in data.get("output") or []:
            if not isinstance(output_item, dict):
                continue
            for content_item in output_item.get("content") or []:
                if not isinstance(content_item, dict):
                    continue
                text_value = content_item.get("text")
                if isinstance(text_value, str):
                    text_parts.append(text_value)
        if text_parts:
            return "\n".join(text_parts)
        raise QwenClientError(
            f"Unexpected {self.provider.title()} Responses API shape: "
            f"{str(data)[:800]}"
        )

    def _raise_if_provider_unavailable(self) -> None:
        """检查供应商熔断状态；参数为空。"""

        with _PROVIDER_CIRCUIT_LOCK:
            message = _PROVIDER_CIRCUITS.get(self.provider)
        if message:
            raise QwenClientError(
                f"{self.provider.title()} 账户或认证异常，已停止重复请求：{message}"
            )

    def _record_provider_error(self, status_code: int, body: str) -> None:
        """记录账户级错误熔断；参数 status_code/body 为 HTTP 响应。"""

        normalized = str(body or "").casefold()
        if status_code == 429:
            _note_concurrency_limit(body)
            return
        fatal = (
            status_code in {401, 402, 403}
            or "arrearage" in normalized
            or "insufficient balance" in normalized
            or "account is in good standing" in normalized
        )
        if not fatal:
            return
        with _PROVIDER_CIRCUIT_LOCK:
            _PROVIDER_CIRCUITS[self.provider] = body[:300]

    def _request_headers(self) -> dict[str, str]:
        """构造本地或云端模型请求头；参数为空。"""

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _post(self, url: str, **kwargs: Any):
        """带并发名额的 POST；参数 url/kwargs 为请求配置。"""

        _acquire_model_slot()
        try:
            return self.session.post(url, **kwargs)
        finally:
            _release_model_slot()

    def token_usage(self) -> dict[str, Any]:
        input_tokens = sum(int(call.get("input_tokens") or 0) for call in self.usage_calls)
        output_tokens = sum(int(call.get("output_tokens") or 0) for call in self.usage_calls)
        total_tokens = sum(int(call.get("total_tokens") or 0) for call in self.usage_calls)
        cache_hit_tokens = sum(
            int(call.get("cache_hit_tokens") or 0)
            for call in self.usage_calls
        )
        cache_miss_tokens = sum(
            int(call.get("cache_miss_tokens") or 0)
            for call in self.usage_calls
        )
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_hit_tokens": cache_hit_tokens,
            "cache_miss_tokens": cache_miss_tokens,
            "calls": self.usage_calls,
        }


QwenClient = StructuredModelClient
ModelClient = StructuredModelClient
ModelClientError = QwenClientError


def _decode_output(value: bytes | None) -> str:
    if not value:
        return ""
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            pass
    return value.decode("utf-8", errors="replace")


def _run_command(command: list[str]) -> subprocess.CompletedProcess:
    completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False, check=False)
    completed.stdout_text = _decode_output(completed.stdout)  # type: ignore[attr-defined]
    completed.stderr_text = _decode_output(completed.stderr)  # type: ignore[attr-defined]
    return completed


def _image_data_url(image_path: Path) -> str:
    import base64

    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".png": "image/png",
    }
    mime_type = mime_types.get(image_path.suffix.lower(), "image/png")
    return f"data:{mime_type};base64,{base64.b64encode(image_path.read_bytes()).decode('ascii')}"


def image_data_url(image_path: Path) -> str:
    """Return a local image as an OpenAI-compatible data URL."""
    return _image_data_url(Path(image_path))


def _render_with_pymupdf(pdf_path: Path, *, zoom: float, max_pages: int, split_pages: bool, include_full_pages: bool) -> list[str]:
    try:
        import fitz
    except ImportError as exc:
        raise QwenClientError("PyMuPDF is not installed") from exc

    urls: list[str] = []
    document = fitz.open(pdf_path)
    try:
        matrix = fitz.Matrix(zoom, zoom)
        for page in document[:max_pages]:
            import base64

            if include_full_pages:
                pixmap = page.get_pixmap(matrix=matrix, alpha=False)
                encoded = pixmap.tobytes("png")
                urls.append(f"data:image/png;base64,{base64.b64encode(encoded).decode('ascii')}")
            if split_pages:
                rect = page.rect
                col_width = rect.width / 2
                row_height = rect.height / 3
                for row in range(3):
                    for col in range(2):
                        clip = fitz.Rect(
                            rect.x0 + col * col_width,
                            rect.y0 + row * row_height,
                            rect.x0 + (col + 1) * col_width,
                            rect.y0 + (row + 1) * row_height,
                        )
                        pixmap = page.get_pixmap(matrix=matrix, alpha=False, clip=clip)
                        encoded = pixmap.tobytes("png")
                        urls.append(f"data:image/png;base64,{base64.b64encode(encoded).decode('ascii')}")
    finally:
        document.close()
    return urls


def _poppler_pdftoppm() -> Path | None:
    candidates = [
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "native"
        / "poppler"
        / "Library"
        / "bin"
        / "pdftoppm.exe",
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "bin"
        / "pdftoppm.cmd",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _pdf_page_sizes(pdf_path: Path, max_pages: int) -> list[tuple[float, float]]:
    executable = _poppler_pdfinfo()
    if not executable:
        return []
    completed = _run_command([str(executable), str(pdf_path)])
    if completed.returncode != 0:
        return []
    sizes: list[tuple[float, float]] = []
    for line in completed.stdout_text.splitlines():  # type: ignore[attr-defined]
        if line.startswith("Page size:"):
            parts = line.replace("Page size:", "").replace("pts", "").split("x")
            if len(parts) >= 2:
                try:
                    width = float(parts[0].strip())
                    height = float(parts[1].strip().split()[0])
                    sizes.append((width, height))
                except ValueError:
                    pass
    if len(sizes) == 1:
        return sizes * max_pages
    return sizes[:max_pages]


def _poppler_pdfinfo() -> Path | None:
    candidate = (
        Path.home()
        / ".cache"
        / "codex-runtimes"
        / "codex-primary-runtime"
        / "dependencies"
        / "native"
        / "poppler"
        / "Library"
        / "bin"
        / "pdfinfo.exe"
    )
    return candidate if candidate.exists() else None


def _render_with_poppler(pdf_path: Path, *, zoom: float, max_pages: int, split_pages: bool, include_full_pages: bool) -> list[str]:
    executable = _poppler_pdftoppm()
    if not executable:
        raise QwenClientError("No PDF renderer found. Install PyMuPDF or Poppler/pdftoppm.")

    dpi = max(int(72 * zoom), 72)
    with tempfile.TemporaryDirectory(prefix="factsheet_pdf_pages_") as temp_dir:
        urls: list[str] = []
        if include_full_pages:
            prefix = Path(temp_dir) / "page"
            command = [
                str(executable),
                "-png",
                "-f",
                "1",
                "-l",
                str(max_pages),
                "-r",
                str(dpi),
                str(pdf_path),
                str(prefix),
            ]
            completed = _run_command(command)
            if completed.returncode != 0:
                output = (completed.stderr_text or completed.stdout_text).strip()  # type: ignore[attr-defined]
                raise QwenClientError(f"pdftoppm failed: {output[:1000]}")
            image_paths = sorted(Path(temp_dir).glob("page-*.png"))
            if not image_paths:
                raise QwenClientError("pdftoppm did not produce any page images")
            urls = [_image_data_url(path) for path in image_paths]
        if not split_pages:
            return urls

        sizes = _pdf_page_sizes(pdf_path, max_pages)
        for page_index, (width_pt, height_pt) in enumerate(sizes, start=1):
            page_width = int(width_pt / 72 * dpi)
            page_height = int(height_pt / 72 * dpi)
            tile_width = page_width // 2
            tile_height = page_height // 3
            for row in range(3):
                for col in range(2):
                    x = col * tile_width
                    y = row * tile_height
                    crop_prefix = Path(temp_dir) / f"page_{page_index}_r{row}_c{col}"
                    crop_command = [
                        str(executable),
                        "-png",
                        "-f",
                        str(page_index),
                        "-l",
                        str(page_index),
                        "-r",
                        str(dpi),
                        "-x",
                        str(x),
                        "-y",
                        str(y),
                        "-W",
                        str(tile_width),
                        "-H",
                        str(tile_height),
                        str(pdf_path),
                        str(crop_prefix),
                    ]
                    completed = _run_command(crop_command)
                    if completed.returncode == 0:
                        urls.extend(_image_data_url(path) for path in sorted(Path(temp_dir).glob(f"page_{page_index}_r{row}_c{col}-*.png")))
        return urls


def pdf_page_image_urls(
    pdf_path: Path,
    *,
    zoom: float = 2.0,
    max_pages: int = 12,
    split_pages: bool = True,
    include_full_pages: bool = True,
) -> list[str]:
    try:
        return _render_with_pymupdf(pdf_path, zoom=zoom, max_pages=max_pages, split_pages=split_pages, include_full_pages=include_full_pages)
    except QwenClientError:
        return _render_with_poppler(pdf_path, zoom=zoom, max_pages=max_pages, split_pages=split_pages, include_full_pages=include_full_pages)


def pdf_region_image_urls(
    pdf_path: Path,
    regions: list[dict[str, Any]],
    *,
    zoom: float = 4.0,
    max_pages: int = 12,
    margin_px: int = 60,
) -> list[dict[str, Any]]:
    """Render cropped page regions as image data URLs.

    Region bboxes can be absolute pixels (`bbox`) or relative 0-1000 coords
    (`relative_bbox`). Returned items preserve the region metadata.
    """
    executable = _poppler_pdftoppm()
    if not executable:
        raise QwenClientError("Region cropping requires Poppler/pdftoppm in this environment")

    dpi = max(int(72 * zoom), 72)
    sizes = _pdf_page_sizes(pdf_path, max_pages)
    if not sizes:
        raise QwenClientError("Cannot determine PDF page sizes for region cropping")

    rendered: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="factsheet_region_crops_") as temp_dir:
        for index, region in enumerate(regions, start=1):
            try:
                page = int(region.get("page") or 1)
            except (TypeError, ValueError):
                page = 1
            if page < 1 or page > len(sizes):
                continue
            width_pt, height_pt = sizes[page - 1]
            page_width = int(width_pt / 72 * dpi)
            page_height = int(height_pt / 72 * dpi)

            bbox = region.get("relative_bbox") or region.get("bbox")
            if not isinstance(bbox, list) or len(bbox) != 4:
                continue
            try:
                x1, y1, x2, y2 = [float(value) for value in bbox]
            except (TypeError, ValueError):
                continue
            if region.get("relative_bbox") or max(abs(x1), abs(y1), abs(x2), abs(y2)) <= 1000:
                x1, x2 = x1 / 1000 * page_width, x2 / 1000 * page_width
                y1, y2 = y1 / 1000 * page_height, y2 / 1000 * page_height

            x = max(int(min(x1, x2)) - margin_px, 0)
            y = max(int(min(y1, y2)) - margin_px, 0)
            right = min(int(max(x1, x2)) + margin_px, page_width)
            bottom = min(int(max(y1, y2)) + margin_px, page_height)
            crop_width = max(right - x, 1)
            crop_height = max(bottom - y, 1)
            crop_prefix = Path(temp_dir) / f"region_{index:03d}_{region.get('type', 'unknown')}"
            command = [
                str(executable),
                "-png",
                "-f",
                str(page),
                "-l",
                str(page),
                "-r",
                str(dpi),
                "-x",
                str(x),
                "-y",
                str(y),
                "-W",
                str(crop_width),
                "-H",
                str(crop_height),
                str(pdf_path),
                str(crop_prefix),
            ]
            completed = _run_command(command)
            if completed.returncode != 0:
                continue
            for path in sorted(Path(temp_dir).glob(f"{crop_prefix.name}-*.png")):
                rendered.append({"region": region, "image_url": _image_data_url(path)})
    return rendered


def pdf_summary_candidate_image_urls(pdf_path: Path, *, zoom: float = 2.0, max_pages: int = 12) -> list[str]:
    executable = _poppler_pdftoppm()
    if not executable:
        return pdf_page_image_urls(pdf_path, zoom=zoom, max_pages=max_pages, split_pages=True, include_full_pages=False)

    dpi = max(int(72 * zoom), 72)
    sizes = _pdf_page_sizes(pdf_path, max_pages)
    urls: list[str] = []
    with tempfile.TemporaryDirectory(prefix="factsheet_summary_crops_") as temp_dir:
        for page_index, (width_pt, height_pt) in enumerate(sizes, start=1):
            page_width = int(width_pt / 72 * dpi)
            page_height = int(height_pt / 72 * dpi)
            tile_width = page_width // 2
            tile_height = page_height // 3
            # Most factsheets place portfolio/statistics summaries in the middle/right blocks.
            regions = [
                ("right_mid", page_width // 2, int(page_height * 0.24), page_width // 2, int(page_height * 0.36)),
                ("right_low", page_width // 2, page_height // 3, page_width // 2, page_height // 3),
                ("left_mid", 0, int(page_height * 0.24), page_width // 2, int(page_height * 0.36)),
            ]
            for label, x, y, width, height in regions:
                crop_prefix = Path(temp_dir) / f"summary_{page_index}_{label}"
                command = [
                    str(executable),
                    "-png",
                    "-f",
                    str(page_index),
                    "-l",
                    str(page_index),
                    "-r",
                    str(dpi),
                    "-x",
                    str(x),
                    "-y",
                    str(y),
                    "-W",
                    str(width),
                    "-H",
                    str(height),
                    str(pdf_path),
                    str(crop_prefix),
                ]
                completed = _run_command(command)
                if completed.returncode == 0:
                    urls.extend(_image_data_url(path) for path in sorted(Path(temp_dir).glob(f"summary_{page_index}_{label}-*.png")))
    return urls


def build_pdf_messages(
    prompt: str,
    pdf_path: Path,
    *,
    max_pages: int = 12,
    zoom: float = 2.0,
    split_pages: bool = True,
    include_full_pages: bool = True,
) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt.replace("<PDF 文件名>", pdf_path.name)}]
    for image_url in pdf_page_image_urls(
        pdf_path,
        zoom=zoom,
        max_pages=max_pages,
        split_pages=split_pages,
        include_full_pages=include_full_pages,
    ):
        content.append({"type": "image_url", "image_url": {"url": image_url}})
    return [
        {"role": "system", "content": "你只输出合法 JSON。"},
        {"role": "user", "content": content},
    ]


def retry_call(func, *, attempts: int, base_delay: float = 2.0):
    last_error: Exception | None = None
    max_attempts = max(1, attempts)
    attempt = 0
    while attempt < max_attempts:
        attempt += 1
        try:
            return func()
        except Exception as exc:  # noqa: BLE001 - preserve API error details for final failure
            last_error = exc
            text = str(exc)
            if provider_unavailable_reason(text):
                break
            if is_rate_limit_error(text):
                max_attempts = max(max_attempts, RATE_LIMIT_MAX_ATTEMPTS)
                if attempt >= max_attempts:
                    break
                delay = min(
                    RATE_LIMIT_MAX_DELAY_SECONDS,
                    RATE_LIMIT_BASE_DELAY_SECONDS * (2 ** (attempt - 1)),
                )
                time.sleep(delay * random.uniform(0.8, 1.2))
                continue
            if attempt >= max_attempts:
                break
            time.sleep(base_delay * (2 ** (attempt - 1)))
    assert last_error is not None
    raise last_error
