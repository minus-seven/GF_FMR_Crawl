#!/usr/bin/env bash
# 安装并验证本地 Qwen 4B；参数为空，依赖已安装的 Ollama。
set -euo pipefail

OLLAMA_BIN="$(command -v ollama 2>/dev/null || true)"
if [[ -z "$OLLAMA_BIN" && -x "$HOME/.local/bin/ollama" ]]; then
  OLLAMA_BIN="$HOME/.local/bin/ollama"
fi
if [[ -z "$OLLAMA_BIN" && -x "/Applications/Ollama.app/Contents/Resources/ollama" ]]; then
  OLLAMA_BIN="/Applications/Ollama.app/Contents/Resources/ollama"
fi
if [[ -z "$OLLAMA_BIN" ]]; then
  echo "未找到 Ollama。请先从 https://ollama.com/download 安装，再重新运行本脚本。" >&2
  exit 1
fi

if ! curl --fail --silent --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null; then
  echo "正在启动 Ollama 本地服务…"
  "$OLLAMA_BIN" serve >/tmp/fundscope-ollama.log 2>&1 &
  sleep 2
fi

if ! "$OLLAMA_BIN" list | awk 'NR > 1 && $1 == "qwen3:4b" { found = 1 } END { exit !found }'; then
  "$OLLAMA_BIN" pull qwen3:4b
fi
curl --fail --silent --max-time 10 http://127.0.0.1:11434/v1/models
echo
echo "本地 Qwen 4B 已就绪：在模型设置中使用 http://127.0.0.1:11434/v1 和 qwen3:4b。"
