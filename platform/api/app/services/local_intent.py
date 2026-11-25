"""本地 Qwen 结构化意图分类器；默认关闭，失败时返回 None。"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.schemas.intent import IntentCandidate, IntentEnvelope
from app.services.model_settings import read_model_settings


def _native_ollama_url(base_url: str) -> str:
    """转换 OpenAI 兼容地址为 Ollama 原生地址；参数 base_url 为配置地址。"""

    parsed = urlsplit(base_url)
    root = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else "http://127.0.0.1:11434"
    return f"{root}/api/chat"


def classify_with_local_qwen(question: str, candidates: list[IntentCandidate]) -> IntentEnvelope | None:
    """调用本地 Qwen 结构化识别意图；参数 question/candidates 为问题和规则候选。"""

    settings = read_model_settings()
    schema = IntentEnvelope.model_json_schema()
    prompt = (
        "只做意图分类和槽位抽取，不回答问题，不调用工具。\n"
        f"候选意图：{json.dumps([item.model_dump(mode='json') for item in candidates], ensure_ascii=False)}\n"
        f"用户问题：{question}"
    )
    payload: dict[str, Any] = {
        "model": settings.local.primary_model,
        "messages": [
            {"role": "system", "content": "你是基金研究问答的意图分类器，只输出符合 JSON Schema 的 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "think": False,
        "format": schema,
        "options": {"temperature": 0, "num_predict": 512},
    }
    started = time.perf_counter()
    try:
        response = httpx.post(
            _native_ollama_url(settings.local.base_url),
            json=payload,
            timeout=8.0,
        )
        response.raise_for_status()
        body = response.json()
        content = ((body.get("message") or {}).get("content") or "").strip()
        envelope = IntentEnvelope.model_validate(json.loads(content))
        return envelope.model_copy(
            update={
                "classifier": "local_qwen",
                "classifier_model": settings.local.primary_model,
                "classifier_latency_ms": int((time.perf_counter() - started) * 1000),
            }
        )
    except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError):
        return None
    finally:
        _ = int((time.perf_counter() - started) * 1000)
