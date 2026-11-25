"""Read and atomically update local external-model configuration."""

from __future__ import annotations

import configparser
import os
from pathlib import Path
import tempfile
import threading
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlparse

from app.api.errors import ApiError
from app.core.config import get_settings
from app.schemas.settings import (
    DeepseekSettingsData,
    LocalModelSettingsData,
    LocalModelHealthData,
    MineruSettingsData,
    ModelSettingsData,
    ModelSettingsUpdate,
    QwenSettingsData,
)


SETTINGS_LOCK = threading.Lock()
SETTINGS = get_settings()
PROJECT_ROOT = SETTINGS.project_root
_CONFIG_OVERRIDE = str(os.environ.get("FACTSHEET_CONFIG") or "").strip()
CONFIG_PATH = (
    Path(_CONFIG_OVERRIDE).expanduser().resolve()
    if _CONFIG_OVERRIDE
    else SETTINGS.state_root / "config.ini"
)
if not CONFIG_PATH.is_file() and (PROJECT_ROOT / "config.ini").is_file():
    CONFIG_PATH = PROJECT_ROOT / "config.ini"


def _mask_secret(value: str) -> str:
    """返回密钥脱敏提示；参数 value 为原始密钥。"""

    text = str(value or "").strip()
    if not text:
        return "未配置"
    if len(text) <= 8:
        return "已配置"
    return f"{text[:3]}***{text[-3:]}"


def _load_parser(path: Path) -> configparser.ConfigParser:
    """读取本地配置文件；参数 path 为配置文件路径。"""

    parser = configparser.ConfigParser(interpolation=None)
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8-sig") as handle:
                parser.read_file(handle)
        except (OSError, configparser.Error) as exc:
            raise ApiError(
                500,
                "E_SETTINGS_READ",
                f"无法读取模型配置：{exc}",
            ) from exc
    defaults = {
        "api": {
            "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "qwen_api_key": "",
            "deepseek_base_url": "https://api.deepseek.com",
            "deepseek_api_key": "",
            "local_model_base_url": "http://127.0.0.1:11434/v1",
            "mineru_api_url": "https://mineru.net/api",
            "mineru_api_token": "",
        },
        "models": {
            "primary_model": "deepseek-flash",
            "fallback_model": "deepseek-v4-pro",
            "vision_model": "deepseek-flash",
            "deepseek_primary_model": "deepseek-flash",
            "deepseek_fallback_model": "deepseek-v4-pro",
            "deepseek_vision_model": "deepseek-flash",
            "qwen_primary_model": "qwen3.7-max",
            "qwen_fallback_model": "qwen3.7-max-2026-06-08",
            "qwen_vision_model": "qwen3.7-plus",
            "local_primary_model": "qwen3:4b",
            "mineru_model": "vlm",
        },
        "processing": {
            "structured_provider": "deepseek",
            "structured_thinking": "false",
            "local_model_workers": "1",
            "mineru_mode": "lightweight",
        },
    }
    for section, values in defaults.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for option, fallback in values.items():
            if not parser.has_option(section, option):
                parser.set(section, option, fallback)
    return parser


def _validate_http_url(value: str, label: str) -> str:
    """校验 HTTP(S) 地址；参数 value/label 为地址和显示名称。"""

    text = str(value or "").strip().rstrip("/")
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ApiError(422, "E_SETTINGS_URL", f"{label}必须是有效的 HTTP(S) 地址")
    return text


def read_model_settings(path: Path | None = None) -> ModelSettingsData:
    """读取本机模型设置真实值；参数 path 为可选配置路径。"""

    parser = _load_parser(path or CONFIG_PATH)
    mineru_token = parser.get("api", "mineru_api_token", fallback="").strip()
    qwen_key = parser.get("api", "qwen_api_key", fallback="").strip()
    deepseek_key = parser.get("api", "deepseek_api_key", fallback="").strip()
    provider = parser.get("processing", "structured_provider", fallback="deepseek").strip()
    if provider not in {"deepseek", "qwen", "local"}:
        provider = "deepseek"
    thinking = parser.get("processing", "structured_thinking", fallback="false").strip().lower()
    mode = parser.get("processing", "mineru_mode", fallback="lightweight").strip()
    if mode not in {"lightweight", "token"}:
        mode = "lightweight"
    return ModelSettingsData(
        mineru=MineruSettingsData(
            mode=mode,
            api_url=parser.get(
                "api",
                "mineru_api_url",
                fallback="https://mineru.net/api",
            ).strip(),
            api_token=mineru_token,
            token_configured=bool(mineru_token),
            token_hint=_mask_secret(mineru_token),
        ),
        structured_provider=provider,
        structured_thinking=thinking in {"1", "true", "yes", "on"},
        deepseek=DeepseekSettingsData(
            base_url=parser.get(
                "api",
                "deepseek_base_url",
                fallback="https://api.deepseek.com",
            ).strip(),
            api_key=deepseek_key,
            api_key_configured=bool(deepseek_key),
            api_key_hint=_mask_secret(deepseek_key),
            primary_model=parser.get(
                "models",
                "deepseek_primary_model",
                fallback="deepseek-flash",
            ).strip(),
            fallback_model=parser.get(
                "models",
                "deepseek_fallback_model",
                fallback="deepseek-v4-pro",
            ).strip(),
            vision_model=parser.get(
                "models",
                "deepseek_vision_model",
                fallback="deepseek-flash",
            ).strip(),
        ),
        qwen=QwenSettingsData(
            base_url=parser.get(
                "api",
                "qwen_base_url",
                fallback="https://dashscope.aliyuncs.com/compatible-mode/v1",
            ).strip(),
            api_key=qwen_key,
            api_key_configured=bool(qwen_key),
            api_key_hint=_mask_secret(qwen_key),
            primary_model=parser.get(
                "models",
                "qwen_primary_model",
                fallback="qwen3.7-max",
            ).strip(),
            fallback_model=parser.get(
                "models",
                "qwen_fallback_model",
                fallback="qwen3.7-max-2026-06-08",
            ).strip(),
            vision_model=parser.get(
                "models",
                "qwen_vision_model",
                fallback="qwen3.7-plus",
            ).strip(),
        ),
        local=LocalModelSettingsData(
            base_url=parser.get(
                "api",
                "local_model_base_url",
                fallback="http://127.0.0.1:11434/v1",
            ).strip(),
            primary_model=parser.get(
                "models",
                "local_primary_model",
                fallback="qwen3:4b",
            ).strip(),
            workers=max(
                1,
                min(
                    2,
                    parser.getint("processing", "local_model_workers", fallback=1),
                ),
            ),
        ),
    )


def update_model_settings(
    body: ModelSettingsUpdate,
    path: Path | None = None,
) -> ModelSettingsData:
    """校验并原子保存模型设置；参数 body/path 为更新内容和配置路径。"""

    target = path or CONFIG_PATH
    mineru_url = _validate_http_url(body.mineru.api_url, "MinerU API 地址")
    deepseek_url = _validate_http_url(body.deepseek.base_url, "DeepSeek API 地址")
    qwen_url = _validate_http_url(body.qwen.base_url, "Qwen API 地址")
    local_model_url = _validate_http_url(body.local.base_url, "本地模型服务地址")
    with SETTINGS_LOCK:
        parser = _load_parser(target)
        existing_token = parser.get("api", "mineru_api_token", fallback="").strip()
        new_token = str(body.mineru.api_token or "").strip()
        if body.mineru.clear_api_token:
            final_token = ""
        elif new_token:
            final_token = new_token
        else:
            final_token = existing_token
        if body.mineru.mode == "token" and not final_token:
            raise ApiError(
                422,
                "E_MINERU_TOKEN_REQUIRED",
                "选择 API Tokens 路线时必须填写 MinerU Token",
            )

        existing_qwen_key = parser.get("api", "qwen_api_key", fallback="").strip()
        existing_deepseek_key = parser.get("api", "deepseek_api_key", fallback="").strip()
        new_deepseek_key = str(body.deepseek.api_key or "").strip()
        if body.deepseek.clear_api_key:
            final_deepseek_key = ""
        elif new_deepseek_key:
            final_deepseek_key = new_deepseek_key
        else:
            final_deepseek_key = existing_deepseek_key
        new_qwen_key = str(body.qwen.api_key or "").strip()
        if body.qwen.clear_api_key:
            final_qwen_key = ""
        elif new_qwen_key:
            final_qwen_key = new_qwen_key
        else:
            final_qwen_key = existing_qwen_key
        selected_key = (
            final_deepseek_key if body.structured_provider == "deepseek"
            else final_qwen_key if body.structured_provider == "qwen" else "local"
        )
        if not selected_key:
            label = "DeepSeek" if body.structured_provider == "deepseek" else "Qwen"
            raise ApiError(
                422,
                "E_STRUCTURED_API_KEY_REQUIRED",
                f"选择 {label} 作为结构化供应商时必须填写 API Key",
            )

        parser.set("api", "mineru_api_url", mineru_url)
        parser.set("api", "mineru_api_token", final_token)
        parser.set("api", "deepseek_base_url", deepseek_url)
        parser.set("api", "deepseek_api_key", final_deepseek_key)
        parser.set("api", "qwen_base_url", qwen_url)
        parser.set("api", "qwen_api_key", final_qwen_key)
        parser.set("api", "local_model_base_url", local_model_url)
        parser.set("models", "deepseek_primary_model", body.deepseek.primary_model.strip())
        parser.set("models", "deepseek_fallback_model", body.deepseek.fallback_model.strip())
        parser.set("models", "deepseek_vision_model", body.deepseek.vision_model.strip())
        parser.set("models", "qwen_primary_model", body.qwen.primary_model.strip())
        parser.set("models", "qwen_fallback_model", body.qwen.fallback_model.strip())
        parser.set("models", "qwen_vision_model", body.qwen.vision_model.strip())
        parser.set("models", "local_primary_model", body.local.primary_model.strip())
        if body.structured_provider == "deepseek":
            selected = body.deepseek
            parser.set("models", "primary_model", selected.primary_model.strip())
            parser.set("models", "fallback_model", selected.fallback_model.strip())
            parser.set("models", "vision_model", selected.vision_model.strip())
        elif body.structured_provider == "qwen":
            selected = body.qwen
            parser.set("models", "primary_model", selected.primary_model.strip())
            parser.set("models", "fallback_model", selected.fallback_model.strip())
            parser.set("models", "vision_model", selected.vision_model.strip())
        else:
            parser.set("models", "primary_model", body.local.primary_model.strip())
            parser.set("models", "fallback_model", body.local.primary_model.strip())
            parser.set("models", "vision_model", "")
        parser.set("processing", "structured_provider", body.structured_provider)
        parser.set("processing", "local_model_workers", str(body.local.workers))
        parser.set(
            "processing",
            "structured_thinking",
            "true" if body.structured_thinking else "false",
        )
        parser.set("processing", "mineru_mode", body.mineru.mode)

        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=target.parent,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(
                    "; Managed by FundScope settings. Local secrets are never returned by the API.\n"
                )
                parser.write(handle)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            raise ApiError(
                500,
                "E_SETTINGS_WRITE",
                f"无法保存模型配置：{exc}",
            ) from exc
    return read_model_settings(target)


def check_local_model_runtime(
    path: Path | None = None,
    *,
    timeout: float = 1.5,
) -> LocalModelHealthData:
    """探测本地 OpenAI 兼容模型服务；参数 path/timeout 为配置路径和超时。"""

    settings = read_model_settings(path)
    base_url = settings.local.base_url.rstrip("/")
    url = f"{base_url}/models"
    try:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=timeout) as response:
            import json

            payload = json.loads(response.read().decode("utf-8"))
        models = [
            str(item.get("id"))
            for item in payload.get("data", [])
            if isinstance(item, dict) and item.get("id")
        ]
        return LocalModelHealthData(
            available=True,
            base_url=base_url,
            models=models,
        )
    except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
        return LocalModelHealthData(
            available=False,
            base_url=base_url,
            reason=str(exc),
        )
