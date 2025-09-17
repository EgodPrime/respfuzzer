import json
from contextlib import closing
from typing import Iterator, List, Optional

from mplfuzz.db.base import get_db_cursor
from mplfuzz.models import API
from mplfuzz.utils.result import Err, Ok, Result, resultify

with get_db_cursor() as cur:
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS api (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_name TEXT, 
            library_name TEXT,
            source TEXT, 
            args TEXT, 
            ret_type TEXT
        )"""
    )


@resultify
def create_api(api: API) -> Result[None, Exception]:
    args_text = json.dumps([arg.model_dump() for arg in api.args])
    with get_db_cursor() as cur:
        cur.execute(
            f"""INSERT INTO api (api_name, library_name, source, args, ret_type) VALUES (?, ?, ?, ?, ?)""",
            (api.api_name, api.library_name, api.source, args_text, api.ret_type),
        )


@resultify
def get_api(api_name: str) -> Result[API | None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM api WHERE api_name = ?", (api_name,))
        row = cur.fetchone()
        if row:
            return API(
                id=row[0], api_name=row[1], library_name=row[2], source=row[3], args=json.loads(row[4]), ret_type=row[5]
            )
        else:
            return None


@resultify
def get_apis(library_name: str | None) -> Result[List[API], Exception]:
    if library_name:
        filter = f"WHERE api_name LIKE '{library_name}.%'"
    else:
        filter = ""
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM api {filter}")
        rows = cur.fetchall()
        api_list = [
            API(id=row[0], api_name=row[1], library_name=row[2], source=row[3], args=json.loads(row[4]), ret_type=row[5])
            for row in rows
        ]
        return api_list


def get_api_iter(library_name: Optional[str]) -> Iterator[API]:
    if library_name:
        filter = f"WHERE api_name LIKE '{library_name}.%'"
    else:
        filter = ""
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM api {filter}")
        for row in cur:
            yield API(
                id=row[0], api_name=row[1], library_name=row[2], source=row[3], args=json.loads(row[4]), ret_type=row[5]
            )
