import json
from typing import List
from mplfuzz.db.base import get_db_cursor
from mplfuzz.utils.result import Err, Ok, Result, resultify
from mplfuzz.models import API


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
            return API(api_name=row[0], library_name=row[1], source=row[2], args=json.loads(row[3]), ret_type=row[4])
        else:
            return None


@resultify
def get_apis(library_name: str | None) -> Result[List[API], Exception]:
    if library_name:
        filter = f"WHERE name LIKE '{library_name}.%'"
    else:
        filter = ""
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM api {filter}")
        rows = cur.fetchall()
        api_list = [API(
            api_name=row[0], 
            library_name=row[1], 
            source=row[2], 
            args=json.loads(row[3]), 
            ret_type=row[4]) for row in rows]
        return api_list
