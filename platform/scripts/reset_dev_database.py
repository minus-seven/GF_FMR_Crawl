"""Safely reset only the D1 development schema."""

from __future__ import annotations

import argparse

import psycopg


def parse_args() -> argparse.Namespace:
    """解析开发库重置参数；参数为空。"""

    parser = argparse.ArgumentParser(
        description="清空开发库 public schema；必须显式提供 --yes。"
    )
    parser.add_argument("--database-url", required=True)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="确认清空当前连接数据库的 public schema。",
    )
    return parser.parse_args()


def main() -> int:
    """执行开发库 schema 重置；参数为空。"""

    args = parse_args()
    if not args.yes:
        raise SystemExit("拒绝执行：请显式传入 --yes")
    with psycopg.connect(args.database_url, autocommit=True) as connection:
        database_name = connection.execute("SELECT current_database()").fetchone()[0]
        if database_name in {"postgres", "template0", "template1"}:
            raise SystemExit(f"拒绝清空系统数据库：{database_name}")
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
    print(f"[OK] reset public schema in {database_name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
