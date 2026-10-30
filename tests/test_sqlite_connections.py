"""Transaction and Windows lock lifetime regressions for API stores."""
import sqlite3

import pytest

from sat_sim.sqlite_connections import connect


def test_transaction_commits_and_closes_without_garbage_collection(tmp_path):
    path = tmp_path / 'database.sqlite3'
    with connect(path) as db:
        db.execute('CREATE TABLE values_table (value INTEGER)')
        db.execute('INSERT INTO values_table VALUES (42)')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        db.execute('SELECT 1')
    with connect(path) as reopened:
        assert reopened.execute('SELECT value FROM values_table').fetchone() == (42,)
    # A live connection reference must not prevent Windows file removal.
    path.unlink()


def test_failed_transaction_rolls_back_and_releases_connection(tmp_path):
    path = tmp_path / 'rollback.sqlite3'
    with connect(path) as db:
        db.execute('CREATE TABLE values_table (value INTEGER)')
    with pytest.raises(RuntimeError, match='abort'):
        with connect(path) as failed:
            failed.execute('INSERT INTO values_table VALUES (42)')
            raise RuntimeError('abort')
    with pytest.raises(sqlite3.ProgrammingError, match='closed'):
        failed.execute('SELECT 1')
    with connect(path) as reopened:
        assert reopened.execute('SELECT count(*) FROM values_table').fetchone() == (0,)
