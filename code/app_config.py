"""Load the project's local API/model configuration without exposing secrets."""

from __future__ import annotations

import configparser
import importlib.util
import os
import re
import shutil
from pathlib import Path
from typing import Any


CODE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = Path(os.environ.get("FACTSHEET_PROJECT_ROOT") or CODE_DIR.parent).resolve()
DEFAULT_STATE_ROOT = PROJECT_ROOT.parent.parent / "fund-research-state"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.ini"  # backward-compatible legacy location
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config.example.ini"
LEGACY_CONFIG_PATH = CODE_DIR / "structured_data" / "local_config.py"

DEFAULTS: dict[str, dict[str, str]] = {
    "api": {
        "qwen_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "qwen_api_key": "",
        "deepseek_base_url": "https://api.deepseek.com",
        "deepseek_api_key": "",
        "local_model_base_url": "http://127.0.0.1:11434/v1",
        "mineru_api_url": "https://mineru.net/api",
        "mineru_api_token": "",
        "fx_api_url": "https://open.er-api.com/v6/latest",
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
        "mineru_mode": "lightweight",
        "mineru_workers": "36",
        "mineru_agent_workers": "18",
        "model_workers": "32",
        "local_model_workers": "1",
        "api_timeout_seconds": "300",
        "api_retry_attempts": "3",
    },
}

ENVIRONMENT_MAP = {
    ("api", "qwen_base_url"): "QWEN_BASE_URL",
    ("api", "qwen_api_key"): "QWEN_API_KEY",
    ("api", "deepseek_base_url"): "DEEPSEEK_BASE_URL",
    ("api", "deepseek_api_key"): "DEEPSEEK_API_KEY",
    ("api", "local_model_base_url"): "LOCAL_MODEL_BASE_URL",
    ("api", "mineru_api_url"): "MINERU_API_URL",
    ("api", "mineru_api_token"): "MINERU_API_TOKEN",
    ("api", "fx_api_url"): "FIXED_INCOME_FX_API_URL",
    ("models", "primary_model"): "FACTSHEET_PRIMARY_MODEL",
    ("models", "fallback_model"): "FACTSHEET_FALLBACK_MODEL",
    ("models", "vision_model"): "FACTSHEET_VISION_MODEL",
    ("models", "deepseek_primary_model"): "DEEPSEEK_PRIMARY_MODEL",
    ("models", "deepseek_fallback_model"): "DEEPSEEK_FALLBACK_MODEL",
    ("models", "deepseek_vision_model"): "DEEPSEEK_VISION_MODEL",
    ("models", "local_primary_model"): "LOCAL_MODEL_NAME",
    ("models", "mineru_model"): "FACTSHEET_MINERU_MODEL",
    ("processing", "mineru_mode"): "FACTSHEET_MINERU_PARSE_MODE",
    ("processing", "structured_provider"): "FACTSHEET_STRUCTURED_PROVIDER",
    ("processing", "structured_thinking"): "FACTSHEET_STRUCTURED_THINKING",
    ("processing", "mineru_workers"): "FACTSHEET_MINERU_WORKERS",
    ("processing", "mineru_agent_workers"): "FACTSHEET_MINERU_AGENT_WORKERS",
    ("processing", "model_workers"): "FACTSHEET_MODEL_WORKERS",
    ("processing", "local_model_workers"): "FACTSHEET_LOCAL_MODEL_WORKERS",
    ("processing", "api_timeout_seconds"): "FACTSHEET_API_TIMEOUT",
    ("processing", "api_retry_attempts"): "FACTSHEET_API_ATTEMPTS",
}


class ConfigError(RuntimeError):
    """Raised when config.ini is missing or malformed."""


def config_path() -> Path:
    override = str(os.environ.get("FACTSHEET_CONFIG") or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if DEFAULT_CONFIG_PATH.is_file():
        return DEFAULT_CONFIG_PATH
    return state_root() / "config.ini"


def state_root() -> Path:
    """Return the mutable state root outside the source tree when configured."""

    override = str(os.environ.get("FACTSHEET_STATE_ROOT") or "").strip()
    if override:
        return Path(os.path.expandvars(os.path.expanduser(override))).resolve()
    return DEFAULT_STATE_ROOT.resolve()


def _new_parser() -> configparser.ConfigParser:
    return configparser.ConfigParser(interpolation=None)


def _read_legacy_values() -> dict[tuple[str, str], str]:
    """Read the old ignored Python config once, only for local migration."""
    if not LEGACY_CONFIG_PATH.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_factsheet_legacy_config", LEGACY_CONFIG_PATH)
    if spec is None or spec.loader is None:
        return {}
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception:
        return {}
    return {
        ("api", "qwen_base_url"): str(getattr(module, "QWEN_BASE_URL", "") or "").strip(),
        ("api", "qwen_api_key"): str(getattr(module, "QWEN_API_KEY", "") or "").strip(),
        ("api", "mineru_api_token"): str(getattr(module, "MINERU_API_TOKEN", "") or "").strip(),
    }


def ensure_config() -> tuple[Path, bool]:
    """Create config.ini atomically and migrate the old local keys when available."""
    path = config_path()
    if path.is_file():
        return path, False
    if not EXAMPLE_CONFIG_PATH.is_file():
        raise ConfigError(f"Missing configuration template: {EXAMPLE_CONFIG_PATH}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".writing")
    shutil.copyfile(EXAMPLE_CONFIG_PATH, temporary)

    template_text = temporary.read_text(encoding="utf-8")
    for (section, option), value in _read_legacy_values().items():
        if value:
            pattern = re.compile(
                rf"(?m)^([ \t]*{re.escape(option)}[ \t]*=[ \t]*).*$",
            )
            template_text, replacements = pattern.subn(
                lambda match: match.group(1) + value,
                template_text,
                count=1,
            )
            if replacements != 1:
                raise ConfigError(f"Configuration template is missing [{section}] {option}")
    temporary.write_text(template_text, encoding="utf-8")
    os.replace(temporary, path)
    return path, True


def load_config(*, create: bool = True) -> configparser.ConfigParser:
    path = config_path()
    if create:
        path, _ = ensure_config()
    if not path.is_file():
        raise ConfigError(f"Configuration file not found: {path}")
    parser = _new_parser()
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (OSError, configparser.Error) as exc:
        raise ConfigError(f"Cannot read {path.name}: {exc}") from exc
    for section, values in DEFAULTS.items():
        if not parser.has_section(section):
            parser.add_section(section)
        for option, value in values.items():
            if not parser.has_option(section, option):
                parser.set(section, option, value)
    return parser


def value(section: str, option: str, default: Any = "") -> str:
    parser = load_config()
    return str(parser.get(section, option, fallback=str(default))).strip()


def integer(section: str, option: str, default: int) -> int:
    raw = value(section, option, default)
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"[{section}] {option} must be an integer, got {raw!r}") from exc


def apply_environment(*, override: bool = False) -> dict[str, str]:
    """Expose config values to existing scripts and all child processes."""
    parser = load_config()
    applied: dict[str, str] = {}
    for (section, option), env_name in ENVIRONMENT_MAP.items():
        configured = str(parser.get(section, option, fallback="")).strip()
        if not configured:
            continue
        if override or not str(os.environ.get(env_name) or "").strip():
            os.environ[env_name] = configured
        applied[env_name] = os.environ.get(env_name, "")
    # Paths are intentionally derived outside config.ini so a release package
    # can be copied between machines without embedding absolute paths.
    os.environ.setdefault("FACTSHEET_STATE_ROOT", str(state_root()))
    os.environ.setdefault("FACTSHEET_DATA_ROOT", str(state_root() / "data"))
    os.environ.setdefault("FACTSHEET_RUNTIME_ROOT", str(state_root() / "runtime"))
    applied.update(
        {
            "FACTSHEET_STATE_ROOT": os.environ["FACTSHEET_STATE_ROOT"],
            "FACTSHEET_DATA_ROOT": os.environ["FACTSHEET_DATA_ROOT"],
            "FACTSHEET_RUNTIME_ROOT": os.environ["FACTSHEET_RUNTIME_ROOT"],
        }
    )
    return applied


def masked(value_: str) -> str:
    text = str(value_ or "").strip()
    if not text:
        return "未填写"
    if len(text) <= 8:
        return "已填写"
    return f"{text[:3]}***{text[-3:]}"


def safe_summary() -> dict[str, str]:
    parser = load_config()
    return {
        "config": str(config_path()),
        "qwen_url": parser.get("api", "qwen_base_url", fallback=""),
        "qwen_key": masked(parser.get("api", "qwen_api_key", fallback="")),
        "deepseek_url": parser.get("api", "deepseek_base_url", fallback=""),
        "deepseek_key": masked(parser.get("api", "deepseek_api_key", fallback="")),
        "mineru_url": parser.get("api", "mineru_api_url", fallback=""),
        "mineru_token": masked(parser.get("api", "mineru_api_token", fallback="")),
        "primary_model": parser.get("models", "primary_model", fallback=""),
        "fallback_model": parser.get("models", "fallback_model", fallback=""),
        "vision_model": parser.get("models", "vision_model", fallback=""),
        "mineru_model": parser.get("models", "mineru_model", fallback=""),
        "mineru_mode": parser.get("processing", "mineru_mode", fallback="lightweight"),
        "structured_provider": parser.get(
            "processing",
            "structured_provider",
            fallback="deepseek",
        ),
    }
