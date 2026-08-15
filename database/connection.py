"""DB接続、保存先決定、スキーマ初期化の公開入口。"""

# ruff: noqa: F401

from .core import DB_PATH, get_connection, init_database

__all__ = ["DB_PATH", "get_connection", "init_database"]
