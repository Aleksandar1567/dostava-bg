"""`sqlite3`, or the bundled `pysqlite3` when the interpreter was built
without it (e.g. pyenv on a machine without libsqlite3-dev)."""

try:
    import sqlite3
except ModuleNotFoundError:  # pragma: no cover - depends on the interpreter
    import pysqlite3 as sqlite3  # type: ignore[no-redef]

__all__ = ["sqlite3"]
