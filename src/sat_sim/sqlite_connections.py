"""SQLite transaction contexts that also release their connection handles.

sqlite3.Connection.__exit__ commits/rolls back but does not close the handle.
Relying on garbage collection leaks Windows file locks and API resources.
"""
from __future__ import annotations

import sqlite3
from typing import Any


class ScopedConnection(sqlite3.Connection):
    def __exit__(self, *args: Any) -> bool:
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def connect(database: Any, **kwargs: Any) -> sqlite3.Connection:
    return sqlite3.connect(database, factory=ScopedConnection, **kwargs)
