"""External model configuration schemas."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class MineruSettingsData(BaseModel):
    """MinerU 的本机配置与真实值。"""

    mode: Literal["lightweight", "token"]
    api_url: str
    api_token: str
    token_configured: bool
    token_hint: str


class ProviderSettingsData(BaseModel):
    """结构化模型供应商的配置与真实值。"""

    base_url: str
    api_key: str
    api_key_configured: bool
    api_key_hint: str
    primary_model: str
    fallback_model: str
    vision_model: str


class DeepseekSettingsData(ProviderSettingsData):
    """DeepSeek 本机配置。"""


class QwenSettingsData(ProviderSettingsData):
    """Qwen 本机配置。"""


class LocalModelSettingsData(BaseModel):
    """本地 Qwen 模型运行时配置。"""

    base_url: str
    primary_model: str
    workers: int = Field(ge=1, le=2)


class LocalModelHealthData(BaseModel):
    """本地模型服务的轻量健康检查结果。"""

    available: bool
    base_url: str
    models: list[str] = Field(default_factory=list)
    reason: str | None = None


class ModelSettingsData(BaseModel):
    """外部模型设置响应。"""

    mineru: MineruSettingsData
    structured_provider: Literal["deepseek", "qwen", "local"]
    structured_thinking: bool
    deepseek: DeepseekSettingsData
    qwen: QwenSettingsData
    local: LocalModelSettingsData


class MineruSettingsUpdate(BaseModel):
    """MinerU 设置更新请求。"""

    mode: Literal["lightweight", "token"]
    api_url: str = Field(min_length=1, max_length=500)
    api_token: str | None = Field(default=None, max_length=4096)
    clear_api_token: bool = False


class ProviderSettingsUpdate(BaseModel):
    """结构化模型供应商设置更新请求。"""

    base_url: str = Field(min_length=1, max_length=1000)
    api_key: str | None = Field(default=None, max_length=4096)
    clear_api_key: bool = False
    primary_model: str = Field(min_length=1, max_length=200)
    fallback_model: str = Field(min_length=1, max_length=200)
    vision_model: str = Field(min_length=1, max_length=200)


class DeepseekSettingsUpdate(ProviderSettingsUpdate):
    """DeepSeek 设置更新请求。"""


class QwenSettingsUpdate(ProviderSettingsUpdate):
    """Qwen 设置更新请求。"""


class LocalModelSettingsUpdate(BaseModel):
    """本地 Qwen 模型设置更新请求。"""

    base_url: str = Field(min_length=1, max_length=1000)
    primary_model: str = Field(min_length=1, max_length=200)
    workers: int = Field(default=1, ge=1, le=2)


class ModelSettingsUpdate(BaseModel):
    """外部模型设置保存请求。"""

    mineru: MineruSettingsUpdate
    structured_provider: Literal["deepseek", "qwen", "local"]
    structured_thinking: bool
    deepseek: DeepseekSettingsUpdate
    qwen: QwenSettingsUpdate
    local: LocalModelSettingsUpdate
