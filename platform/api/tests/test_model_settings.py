"""Tests for local external-model configuration."""

from pathlib import Path

import pytest

from app.api.errors import ApiError
from app.schemas.settings import (
    DeepseekSettingsUpdate,
    LocalModelSettingsUpdate,
    MineruSettingsUpdate,
    ModelSettingsUpdate,
    QwenSettingsUpdate,
)
from app.services.model_settings import read_model_settings, update_model_settings


def _config(
    path: Path,
    *,
    token: str = "",
    key: str = "",
    deepseek_key: str = "",
) -> Path:
    """写入测试配置；参数 path/token/key/deepseek_key 为路径和已有密钥。"""

    path.write_text(
        "\n".join(
            [
                "[api]",
                "qwen_base_url = https://example.com/v1",
                f"qwen_api_key = {key}",
                "deepseek_base_url = https://api.deepseek.com",
                f"deepseek_api_key = {deepseek_key}",
                "local_model_base_url = http://127.0.0.1:11434/v1",
                "mineru_api_url = https://mineru.example.com/api",
                f"mineru_api_token = {token}",
                "[models]",
                "primary_model = deepseek-flash",
                "fallback_model = deepseek-v4-pro",
                "vision_model = deepseek-flash",
                "deepseek_primary_model = deepseek-flash",
                "deepseek_fallback_model = deepseek-v4-pro",
                "deepseek_vision_model = deepseek-flash",
                "qwen_primary_model = qwen-primary",
                "qwen_fallback_model = qwen-fallback",
                "qwen_vision_model = qwen-vision",
                "local_primary_model = qwen3:4b",
                "[processing]",
                "structured_provider = deepseek",
                "structured_thinking = false",
                "mineru_mode = lightweight",
                "local_model_workers = 1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _body(*, mode: str = "lightweight", api_token: str | None = None) -> ModelSettingsUpdate:
    """构造模型设置请求；参数 mode/api_token 为 MinerU 路线和可选 Token。"""

    return ModelSettingsUpdate(
        mineru=MineruSettingsUpdate(
            mode=mode,
            api_url="https://mineru.example.com/api",
            api_token=api_token,
            clear_api_token=False,
        ),
        structured_provider="deepseek",
        structured_thinking=False,
        deepseek=DeepseekSettingsUpdate(
            base_url="https://api.deepseek.com",
            api_key=api_token,
            clear_api_key=False,
            primary_model="deepseek-flash",
            fallback_model="deepseek-v4-pro",
            vision_model="deepseek-flash",
        ),
        qwen=QwenSettingsUpdate(
            base_url="https://example.com/v1",
            api_key=None,
            clear_api_key=False,
            primary_model="qwen-primary",
            fallback_model="qwen-fallback",
            vision_model="qwen-vision",
        ),
        local=LocalModelSettingsUpdate(
            base_url="http://127.0.0.1:11434/v1",
            primary_model="qwen3:4b",
            workers=1,
        ),
    )


def test_settings_read_returns_real_local_secrets(tmp_path: Path) -> None:
    """本机设置接口应回填真实 Token 和 Key。"""

    path = _config(
        tmp_path / "config.ini",
        token="token-12345678",
        key="key-12345678",
        deepseek_key="deepseek-key-123",
    )
    result = read_model_settings(path)
    assert result.mineru.token_configured is True
    assert result.qwen.api_key_configured is True
    assert result.deepseek.api_key_configured is True
    assert result.mineru.api_token == "token-12345678"
    assert result.qwen.api_key == "key-12345678"
    assert result.deepseek.api_key == "deepseek-key-123"


def test_settings_save_is_atomic_private_and_preserves_blank_secret(
    tmp_path: Path,
) -> None:
    """留空密钥应保留原值，并保存为仅当前用户可读写。"""

    path = _config(
        tmp_path / "config.ini",
        token="old-token-123",
        key="old-key-123",
        deepseek_key="old-deepseek-key",
    )
    updated = update_model_settings(_body(mode="token"), path)
    content = path.read_text(encoding="utf-8")

    assert updated.mineru.mode == "token"
    assert "old-token-123" in content
    assert "old-key-123" in content
    assert "old-deepseek-key" in content
    assert path.stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob(".config.ini.*"))


def test_token_mode_requires_a_token(tmp_path: Path) -> None:
    """Token 路线没有 Token 时应拒绝保存。"""

    path = _config(
        tmp_path / "config.ini",
        deepseek_key="deepseek-key",
    )
    with pytest.raises(ApiError, match="必须填写 MinerU Token"):
        update_model_settings(_body(mode="token"), path)


def test_deepseek_provider_requires_its_own_key(tmp_path: Path) -> None:
    """选择 DeepSeek 时不能借用 Qwen Key。"""

    path = _config(
        tmp_path / "config.ini",
        key="qwen-only-key",
    )
    with pytest.raises(ApiError, match="DeepSeek 作为结构化供应商"):
        update_model_settings(_body(), path)
