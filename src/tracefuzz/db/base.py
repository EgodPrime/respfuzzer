import sqlite3
from contextlib import contextmanager

from tracefuzz.utils.config import get_config
from tracefuzz.utils.paths import RUNDATA_DIR

config = get_config("db_config")
db_name = config.get("db_name") + ".db"
db_path = RUNDATA_DIR.joinpath(db_name)


@contextmanager
def get_db_cursor(commit: bool = True):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    try:
        yield cur
        if commit:
            conn.commit()
    finally:
        cur.close()
        conn.close()
