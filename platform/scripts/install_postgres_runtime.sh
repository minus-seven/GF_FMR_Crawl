#!/usr/bin/env bash
set -euo pipefail

PLATFORM_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_ROOT="$(cd "$PLATFORM_ROOT/.." && pwd)"
STATE_ROOT="${FACTSHEET_STATE_ROOT:-$PROJECT_ROOT/../../fund-research-state}"
RUNTIME_DIR="${PLATFORM_RUNTIME_ROOT:-$STATE_ROOT/platform-runtime}"
PG_DIR="$RUNTIME_DIR/postgres"

if [[ -x "$PG_DIR/bin/postgres" ]]; then
    "$PG_DIR/bin/postgres" --version
    echo "PostgreSQL runtime already installed: $PG_DIR"
    exit 0
fi

if [[ "$(uname -s)" != "Darwin" || "$(uname -m)" != "arm64" ]]; then
    echo "This installer currently supports macOS arm64 only." >&2
    exit 2
fi

TEMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/gf-fmr-postgres.XXXXXX")"
trap 'rm -rf "$TEMP_DIR"' EXIT

curl -Ls \
    https://micro.mamba.pm/api/micromamba/osx-arm64/latest \
    -o "$TEMP_DIR/micromamba.tar.bz2"
mkdir -p "$TEMP_DIR/bin"
tar -xjf "$TEMP_DIR/micromamba.tar.bz2" -C "$TEMP_DIR" bin/micromamba

"$TEMP_DIR/bin/micromamba" create \
    -y \
    -p "$PG_DIR" \
    -c conda-forge \
    "postgresql=17" \
    pgvector

"$PG_DIR/bin/postgres" --version
echo "PostgreSQL runtime installed: $PG_DIR"
