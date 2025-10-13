import json
from typing import Iterator, List, Optional

from tracefuzz.db.base import get_db_cursor
from tracefuzz.models import Function

with get_db_cursor() as cur:
    cur.execute(
        f"""CREATE TABLE IF NOT EXISTS function (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            func_name TEXT, 
            library_name TEXT,
            source TEXT, 
            args TEXT, 
            ret_type TEXT
        )"""
    )


def create_function(function: Function) -> int:
    args_text = json.dumps([arg.model_dump() for arg in function.args])
    with get_db_cursor() as cur:
        cur.execute(
            f"""INSERT INTO function (func_name, library_name, source, args, ret_type) VALUES (?, ?, ?, ?, ?)""",
            (
                function.func_name,
                function.library_name,
                function.source,
                args_text,
                function.ret_type,
            ),
        )
        return cur.lastrowid


def get_function(func_name: str) -> Optional[Function]:
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM function WHERE func_name = ?", (func_name,))
        row = cur.fetchone()
        if row:
            return Function(
                id=row[0],
                func_name=row[1],
                library_name=row[2],
                source=row[3],
                args=json.loads(row[4]),
                ret_type=row[5],
            )
        else:
            return None


def get_functions(library_name: str | None) -> List[Function]:
    if library_name:
        filter = f"WHERE func_name LIKE '{library_name}.%'"
    else:
        filter = ""
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM function {filter}")
        rows = cur.fetchall()
        function_list = [
            Function(
                id=row[0],
                func_name=row[1],
                library_name=row[2],
                source=row[3],
                args=json.loads(row[4]),
                ret_type=row[5],
            )
            for row in rows
        ]
        return function_list


def get_function_iter(library_name: Optional[str]) -> Iterator[Function]:
    if library_name:
        filter = f"WHERE func_name LIKE '{library_name}.%'"
    else:
        filter = ""
    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM function {filter}")
        for row in cur:
            yield Function(
                id=row[0],
                func_name=row[1],
                library_name=row[2],
                source=row[3],
                args=json.loads(row[4]),
                ret_type=row[5],
            )
