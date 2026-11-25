"""Verify equity holding names and tickers with a persistent security cache."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Any, Iterator
import unicodedata

try:
    from structured_data.qwen_client import retry_call
except ImportError:  # pragma: no cover
    from qwen_client import retry_call


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CACHE_PATH = PROJECT_ROOT / "data" / "_cache" / "equity_security_master.json"
_CACHE_CONDITION = threading.Condition()
_INFLIGHT_KEYS: set[str] = set()
_TICKER_SUFFIXES = frozenset(
    {
        "AS",
        "AX",
        "BK",
        "BR",
        "BU",
        "CO",
        "DE",
        "HK",
        "IR",
        "JO",
        "JP",
        "KL",
        "KS",
        "L",
        "LS",
        "MC",
        "ME",
        "MI",
        "MX",
        "NS",
        "NSE",
        "OL",
        "PA",
        "SA",
        "SH",
        "SI",
        "SW",
        "SZ",
        "T",
        "TO",
        "TW",
        "TWO",
        "US",
        "VI",
    }
)
_TICKER_PATTERN = re.compile(
    rf"^(?P<symbol>[A-Z0-9][A-Z0-9&.\-]{{0,15}})\.(?P<suffix>{'|'.join(sorted(_TICKER_SUFFIXES))})$"
)


def _parse_json_object(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("Ticker resolver did not return a JSON object")
    return value


def _security_key(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[\W_]+", "", text, flags=re.UNICODE)


def _normalize_ticker(value: Any) -> str | None:
    """Return a canonical stock ticker, rejecting identifiers that are not equities."""

    text = unicodedata.normalize("NFKC", str(value or "")).strip().upper()
    text = re.sub(r"\s+", "", text)
    match = _TICKER_PATTERN.fullmatch(text)
    if not match:
        return None
    symbol = match.group("symbol")
    suffix = match.group("suffix")
    if suffix in {"SH", "SZ"} and not re.fullmatch(r"\d{6}", symbol):
        return None
    if suffix == "HK":
        if not re.fullmatch(r"\d{1,5}", symbol):
            return None
        symbol = symbol.zfill(4)
    if suffix in {"TW", "TWO"} and not re.fullmatch(r"\d{4,6}[A-Z]?", symbol):
        return None
    return f"{symbol}.{suffix}"


def _ticker_cache_key(value: Any) -> str | None:
    ticker = _normalize_ticker(value)
    return f"ticker:{ticker}" if ticker else None


def _empty_cache() -> dict[str, Any]:
    return {"version": 1, "updated_at": None, "securities": {}}


def _read_cache(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return _empty_cache()
    if not isinstance(value, dict) or not isinstance(value.get("securities"), dict):
        return _empty_cache()
    return value


@contextmanager
def _cache_file_lock(path: Path, timeout: float = 15.0) -> Iterator[None]:
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock_path.stat().st_mtime > 120:
                    lock_path.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for security cache lock: {lock_path}")
            time.sleep(0.05)
    try:
        os.write(descriptor, f"{os.getpid()}\n".encode("ascii"))
        yield
    finally:
        os.close(descriptor)
        lock_path.unlink(missing_ok=True)


def _write_verified_cache(path: Path, entries: dict[str, dict[str, Any]]) -> None:
    if not entries:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with _cache_file_lock(path):
        cache = _read_cache(path)
        securities = cache.setdefault("securities", {})
        securities.update(entries)
        cache["updated_at"] = datetime.now(timezone.utc).isoformat()
        temporary = path.with_suffix(path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)


def _cache_entry_is_usable(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and _normalize_ticker(value.get("ticker")) is not None
        and bool(str(value.get("name_zh") or "").strip())
        and value.get("confidence") == "high"
    )


def _holding_cache_keys(item: dict[str, Any]) -> list[str]:
    candidates = (
        _security_key(item.get("name")),
        _ticker_cache_key(item.get("ticker")),
        _security_key(item.get("name_zh")),
    )
    return list(dict.fromkeys(key for key in candidates if key))


def _ticker_alias_entries(securities: dict[str, Any]) -> dict[str, dict[str, Any]]:
    aliases: dict[str, dict[str, Any]] = {}
    for value in securities.values():
        if not _cache_entry_is_usable(value):
            continue
        alias = _ticker_cache_key(value.get("ticker"))
        if alias:
            aliases.setdefault(alias, dict(value))
    return aliases


def _resolve_cached_entry(
    securities: dict[str, Any],
    ticker_aliases: dict[str, dict[str, Any]],
    item: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    for key in _holding_cache_keys(item):
        if key.startswith("ticker:"):
            value = ticker_aliases.get(key)
            source = "ticker"
        else:
            value = securities.get(key)
            source = "name"
        if _cache_entry_is_usable(value):
            return dict(value), source
    return None, None


def _direct_ticker_answer(holding: dict[str, Any]) -> dict[str, Any] | None:
    """采用已通过格式校验的报告 Ticker；参数 holding 为持仓行。"""

    ticker = _normalize_ticker(holding.get("ticker"))
    name = str(holding.get("name") or holding.get("name_zh") or "").strip()
    if not ticker or not name:
        return None
    if not re.search(r"[\u3400-\u9fff]", name):
        return None
    return {"ticker": ticker, "name_zh": name, "confidence": "high"}


def _ticker_answer_is_confident(answer: dict[str, Any] | None) -> bool:
    """判断持仓答案是否已足够可靠；参数 answer 为模型或直接解析结果。"""

    return bool(
        answer
        and answer.get("ticker")
        and answer.get("name_zh")
        and str(answer.get("confidence") or "").lower() == "high"
    )


def _ticker_prompt(
    asset_class: str,
    request_items: list[dict[str, Any]],
    *,
    allow_tools: bool,
) -> str:
    """构造 Ticker 核对提示；参数 asset_class/items/allow_tools 为请求上下文。"""

    tool_note = (
        "本轮可以使用联网搜索与网页抓取工具，但只在公司身份、上市地、代码或中文名称"
        "存在不确定性时调用；腾讯、苹果等你能高置信确认的知名公司无需搜索。"
        if allow_tools
        else "本轮不启用联网搜索；只根据持仓名称和报告代码判断。不能确定时填 medium 或 low。"
    )
    return f"""
你是权益证券持仓核对模块。以下是{asset_class}基金中尚未命中本地证券主数据缓存的持仓。
请逐项核对标准简体中文名称和 Ticker。{tool_note}
月报里的名称和代码只能作为线索；通过交易所后缀和代码格式校验的报告 Ticker 可以优先采用。

规则：
1. 股票输出该持仓实际对应上市地的交易所 Ticker，并带市场后缀，例如 0700.HK、
   9988.HK、AAPL.US、600519.SH、300750.SZ；不要擅自改成同一公司的另一上市地。
2. name_zh 使用市场通行或公司官方简体中文名。没有可靠中文名时保留报告原名，
   不要生硬直译、缩写扩写或改成另一家公司。
3. 不要把 ISIN、SEDOL、CUSIP、Bloomberg 复合代码、票息、到期日、基金份额类别、
   行业缩写或普通英文缩写误当作股票 Ticker。
4. confidence 只有在公司身份、上市地、中文名和 Ticker 都明确时才能填 high；
   有任何歧义填 medium 或 low。无法可靠识别时 ticker 输出 null，绝不编造。
5. 必须逐项返回原 index，不得遗漏，只输出合法 JSON。

输入：
{json.dumps(request_items, ensure_ascii=False)}

输出格式：
{{"items":[{{"index":1,"name_zh":"腾讯控股或原名称","ticker":"0700.HK或null",
"confidence":"high|medium|low"}}]}}
""".strip()


def _cache_entries_for_ticker(
    key: str,
    answer: dict[str, Any],
    *,
    model: str,
    source: str,
) -> dict[str, dict[str, Any]]:
    """生成可写入证券缓存的条目；参数 key/answer/model/source 为答案上下文。"""

    ticker = answer.get("ticker")
    name_zh = str(answer.get("name_zh") or "").strip()
    if not ticker or not name_zh or str(answer.get("confidence") or "").lower() != "high":
        return {}
    entry = {
        **answer,
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "model": model,
        "source": source,
    }
    entries = {key: entry}
    canonical_key = _security_key(name_zh)
    if canonical_key:
        entries[canonical_key] = entry
    ticker_key = _ticker_cache_key(ticker)
    if ticker_key:
        entries[ticker_key] = entry
    return entries


def _request_ticker_answers(
    client: Any,
    keys: list[str],
    representative_by_key: dict[str, tuple[int, dict[str, Any]]],
    *,
    model: str,
    attempts: int,
    asset_class: str,
    allow_tools: bool,
    enable_thinking: bool,
    call_name: str,
) -> tuple[dict[str, dict[str, Any]], str | None]:
    """执行一次 Ticker 模型核对；参数为请求、模型与 thinking 配置。"""

    if not keys:
        return {}, None
    key_by_index = {
        representative_by_key[key][0]: key
        for key in keys
    }
    request_items = [
        {
            "index": representative_by_key[key][0],
            "holding_name": representative_by_key[key][1].get("name"),
            "report_ticker_candidate": representative_by_key[key][1].get("ticker"),
        }
        for key in keys
    ]
    prompt = _ticker_prompt(asset_class, request_items, allow_tools=allow_tools)
    try:
        response_text = retry_call(
            lambda: client.respond(
                model=model,
                input_text="你只输出合法 JSON，不要解释。\n\n" + prompt,
                tools=(
                    [{"type": "web_search"}, {"type": "web_extractor"}]
                    if allow_tools
                    else None
                ),
                enable_thinking=enable_thinking,
                call_name=call_name,
            ),
            attempts=attempts,
        )
        result = _parse_json_object(response_text)
    except Exception as exc:  # noqa: BLE001
        return {}, f"{type(exc).__name__}: {exc}"
    answers: dict[str, dict[str, Any]] = {}
    for answer in result.get("items") or []:
        if not isinstance(answer, dict):
            continue
        try:
            request_index = int(answer.get("index"))
            key = key_by_index[request_index]
        except (TypeError, ValueError, KeyError):
            continue
        answers[key] = {
            "ticker": _normalize_ticker(answer.get("ticker")),
            "name_zh": str(answer.get("name_zh") or "").strip() or None,
            "confidence": str(answer.get("confidence") or "").strip().lower(),
        }
    return answers, None


def resolve_holding_tickers(
    client: Any,
    holdings: list[dict[str, Any]],
    *,
    model: str,
    attempts: int,
    asset_class: str,
    cache_path: Path | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Resolve only uncached equity securities; let Qwen decide whether web search is needed."""

    if not holdings:
        return holdings, None
    cache_path = Path(cache_path) if cache_path is not None else DEFAULT_CACHE_PATH
    keys = [
        (_holding_cache_keys(item) or [f"row:{index}"])[0]
        for index, item in enumerate(holdings)
    ]
    unique_keys = list(dict.fromkeys(key for key in keys if key))

    with _CACHE_CONDITION:
        cache = _read_cache(cache_path)
        securities = cache.get("securities") or {}
        ticker_aliases = _ticker_alias_entries(securities)
        representative_by_key: dict[str, tuple[int, dict[str, Any]]] = {}
        for index, (key, holding) in enumerate(zip(keys, holdings), start=1):
            if key and key not in representative_by_key:
                representative_by_key[key] = (index, holding)
        cached_answers: dict[str, dict[str, Any]] = {}
        cache_hit_sources: dict[str, str] = {}
        for key, (_, holding) in representative_by_key.items():
            answer, source = _resolve_cached_entry(securities, ticker_aliases, holding)
            if answer is not None and source:
                cached_answers[key] = answer
                cache_hit_sources[key] = source
        direct_answers: dict[str, dict[str, Any]] = {}
        owned_keys: list[str] = []
        waiting_keys: list[str] = []
        for key in unique_keys:
            if key in cached_answers:
                continue
            direct = _direct_ticker_answer(representative_by_key[key][1])
            if direct is not None:
                direct_answers[key] = direct
                continue
            if key in _INFLIGHT_KEYS:
                waiting_keys.append(key)
            else:
                _INFLIGHT_KEYS.add(key)
                owned_keys.append(key)

    result: dict[str, Any] = {"items": []}
    resolved_answers: dict[str, dict[str, Any]] = {
        **cached_answers,
        **direct_answers,
    }
    stored_entries: dict[str, dict[str, Any]] = {}
    for key, answer in direct_answers.items():
        stored_entries.update(
            _cache_entries_for_ticker(
                key,
                answer,
                model="report",
                source="report_ticker",
            )
        )
    errors: list[str] = []
    light_requests = 0
    reasoned_requests = 0

    def persist(entries: dict[str, dict[str, Any]]) -> None:
        """写入缓存并记录失败，避免缓存锁问题中断整只基金。"""

        if not entries:
            return
        try:
            _write_verified_cache(cache_path, entries)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"cache: {type(exc).__name__}: {exc}")

    try:
        persist(stored_entries)
        if owned_keys and getattr(client, "provider", "") != "local":
            light_requests = len(owned_keys)
            light_answers, light_error = _request_ticker_answers(
                client,
                owned_keys,
                representative_by_key,
                model=model,
                attempts=attempts,
                asset_class=asset_class,
                allow_tools=False,
                enable_thinking=False,
                call_name="holding_ticker_resolution_light",
            )
            if light_error:
                errors.append(light_error)
            for key, answer in light_answers.items():
                resolved_answers[key] = answer
                stored_entries.update(
                    _cache_entries_for_ticker(
                        key,
                        answer,
                        model=model,
                        source="qwen_responses_light",
                    )
                )
            persist(stored_entries)

            unresolved_keys = [
                key
                for key in owned_keys
                if not _ticker_answer_is_confident(resolved_answers.get(key))
            ]
            if unresolved_keys:
                reasoned_requests = len(unresolved_keys)
                reasoned_answers, reasoned_error = _request_ticker_answers(
                    client,
                    unresolved_keys,
                    representative_by_key,
                    model=model,
                    attempts=attempts,
                    asset_class=asset_class,
                    allow_tools=True,
                    enable_thinking=True,
                    call_name="holding_ticker_resolution",
                )
                if reasoned_error:
                    errors.append(reasoned_error)
                for key, answer in reasoned_answers.items():
                    resolved_answers[key] = answer
                    stored_entries.update(
                        _cache_entries_for_ticker(
                            key,
                            answer,
                            model=model,
                            source="qwen_responses_auto_tools",
                        )
                    )
                if reasoned_answers:
                    persist(stored_entries)
    finally:
        with _CACHE_CONDITION:
            for key in owned_keys:
                _INFLIGHT_KEYS.discard(key)
            _CACHE_CONDITION.notify_all()

    if waiting_keys:
        deadline = time.monotonic() + 180
        with _CACHE_CONDITION:
            while any(key in _INFLIGHT_KEYS for key in waiting_keys) and time.monotonic() < deadline:
                _CACHE_CONDITION.wait(timeout=0.25)
        latest = _read_cache(cache_path).get("securities") or {}
        latest_ticker_aliases = _ticker_alias_entries(latest)
        for key in waiting_keys:
            holding = representative_by_key[key][1]
            answer, source = _resolve_cached_entry(
                latest,
                latest_ticker_aliases,
                holding,
            )
            if answer is not None and source:
                resolved_answers[key] = answer
                cache_hit_sources[key] = source

    enriched: list[dict[str, Any]] = []
    for key, item in zip(keys, holdings):
        updated = dict(item)
        answer = resolved_answers.get(key) or {}
        updated["ticker"] = answer.get("ticker")
        if answer.get("name_zh"):
            updated["name"] = answer["name_zh"]
        enriched.append(updated)
    result["_cache"] = {
        "path": str(cache_path),
        "hits": sum(1 for key in keys if key in cached_answers),
        "name_hits": sum(
            1
            for key in keys
            if cached_answers.get(key) is not None
            and cache_hit_sources.get(key) == "name"
        ),
        "ticker_hits": sum(
            1
            for key in keys
            if cached_answers.get(key) is not None
            and cache_hit_sources.get(key) == "ticker"
        ),
        "requested": len(owned_keys),
        "direct": len(direct_answers),
        "light_requests": light_requests,
        "reasoned_requests": reasoned_requests,
        "waited": len(waiting_keys),
        "stored": len(stored_entries),
        "local_model_skipped": (
            len(owned_keys) if getattr(client, "provider", "") == "local" else 0
        ),
    }
    if errors:
        result["_error"] = "; ".join(dict.fromkeys(errors))
    return enriched, result
