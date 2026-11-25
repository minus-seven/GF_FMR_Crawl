"""Create the local PostgreSQL database and optional read-only role."""

from __future__ import annotations

import argparse
import os

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo


def parse_args() -> argparse.Namespace:
    """解析数据库初始化参数；参数为空。"""

    parser = argparse.ArgumentParser(description="初始化 D1 原生 PostgreSQL 数据库。")
    parser.add_argument(
        "--admin-url",
        default=os.environ.get("PLATFORM_ADMIN_DATABASE_URL", "postgresql:///postgres"),
        help="具备建库权限的管理连接地址。",
    )
    parser.add_argument(
        "--database",
        default=os.environ.get("PLATFORM_DATABASE_NAME", "fund_research"),
        help="目标数据库名。",
    )
    parser.add_argument(
        "--readonly-role",
        default=os.environ.get("PLATFORM_READONLY_ROLE", "fund_research_ro"),
        help="Tableau 只读角色名。",
    )
    parser.add_argument(
        "--readonly-password",
        default=os.environ.get("PLATFORM_READONLY_PASSWORD", ""),
        help="Tableau 只读账号密码；为空时创建无密码本地角色。",
    )
    return parser.parse_args()


def main() -> int:
    """创建数据库、pgvector 扩展和只读角色；参数为空。"""

    args = parse_args()
    with psycopg.connect(args.admin_url, autocommit=True) as connection:
        version = connection.execute("SHOW server_version_num").fetchone()[0]
        if int(version) < 150000:
            raise RuntimeError(f"需要 PostgreSQL 15+，当前版本号：{version}")
        exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s",
            (args.database,),
        ).fetchone()
        if not exists:
            connection.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(args.database))
            )

        role_exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s",
            (args.readonly_role,),
        ).fetchone()
        if not role_exists:
            password_clause = (
                sql.SQL(" PASSWORD {}").format(sql.Literal(args.readonly_password))
                if args.readonly_password
                else sql.SQL("")
            )
            connection.execute(
                sql.SQL("CREATE ROLE {} LOGIN{}").format(
                    sql.Identifier(args.readonly_role),
                    password_clause,
                )
            )
        elif args.readonly_password:
            connection.execute(
                sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                    sql.Identifier(args.readonly_role),
                    sql.Literal(args.readonly_password),
                )
            )

    database_params = conninfo_to_dict(args.admin_url)
    database_params["dbname"] = args.database
    database_url = make_conninfo(**database_params)
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
        connection.execute(
            sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                sql.Identifier(args.database),
                sql.Identifier(args.readonly_role),
            )
        )
        connection.execute(
            sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(
                sql.Identifier(args.readonly_role)
            )
        )
    print(
        f"[OK] database={args.database} pgvector=enabled "
        f"readonly_role={args.readonly_role}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
