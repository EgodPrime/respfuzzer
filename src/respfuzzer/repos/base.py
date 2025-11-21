from contextlib import contextmanager

import psycopg2

from respfuzzer.utils.config import get_config
from respfuzzer.utils.paths import RUNDATA_DIR

config = get_config("db_config")
db_name = config.get("db_name") + ".db"
db_path = RUNDATA_DIR.joinpath(db_name)


# @contextmanager
# def get_db_cursor(commit: bool = True):
#     """
#     Context manager for SQLite DB cursor.
#     Args:
#         commit (bool): Whether to commit after usage. Default True.
#     Yields:
#         sqlite3.Cursor: Database cursor object.
#     """
#     conn = sqlite3.connect(db_path)
#     cur = conn.cursor()
#     try:
#         yield cur
#         if commit:
#             conn.commit()
#     finally:
#         cur.close()
#         conn.close()


@contextmanager
def get_db_cursor(commit: bool = True):
    """
    Context manager for PostgreSQL DB cursor.
    Args:
        commit (bool): Whether to commit after usage. Default True.
    Yields:
        psycopg2.extensions.cursor: Database cursor object.
    """
    conn = psycopg2.connect(
        dbname=config.get("db_name"),
        user=config.get("user"),
        password=config.get("password"),
        host=config.get("host"),
        port=config.get("port"),
    )
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    finally:
        cur.close()
        conn.close()
