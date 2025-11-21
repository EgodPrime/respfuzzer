"""
这是一个用于管理变异体（mutant）数据的模块。
它定义了 Mutant 数据模型，并提供了与数据库交互的基本功能（增删改查）。
"""

import json
import threading
from typing import Optional

from respfuzzer.models import Mutant
from respfuzzer.repos.base import get_db_cursor

with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS mutant (
            id SERIAL PRIMARY KEY,
            func_id INTEGER,
            seed_id INTEGER,
            library_name TEXT,
            func_name TEXT,
            args TEXT,
            function_call TEXT
        )"""
    )

_db_lock = threading.Lock()


def create_mutant(mutant: Mutant) -> Optional[int]:
    args_text = json.dumps([arg.model_dump() for arg in mutant.args])
    with _db_lock:
        with get_db_cursor() as cur:
            cur.execute(
                """INSERT INTO mutant (func_id, seed_id, library_name, func_name, args, function_call)
                   VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
                (
                    mutant.func_id,
                    mutant.seed_id,
                    mutant.library_name,
                    mutant.func_name,
                    args_text,
                    mutant.function_call,
                ),
            )
            row = cur.fetchone()
            return row[0] if row is not None else None


def delete_mutant(mutant_id: int) -> None:
    with _db_lock:
        with get_db_cursor() as cur:
            cur.execute("DELETE FROM mutant WHERE id = %s", (mutant_id,))


def get_mutant(mutant_id: int) -> Optional[Mutant]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM mutant WHERE id = %s", (mutant_id,))
        row = cur.fetchone()
        if row:
            return Mutant(
                id=row[0],
                func_id=row[1],
                seed_id=row[2],
                library_name=row[3],
                func_name=row[4],
                args=json.loads(row[5]),
                function_call=row[6],
            )
        else:
            return None


def update_mutant(mutant: Mutant) -> None:
    args_text = json.dumps([arg.model_dump() for arg in mutant.args])
    with _db_lock:
        with get_db_cursor() as cur:
            cur.execute(
                """UPDATE mutant
                   SET func_id = %s, seed_id = %s, library_name = %s, func_name = %s, args = %s, function_call = %s
                   WHERE id = %s""",
                (
                    mutant.func_id,
                    mutant.seed_id,
                    mutant.library_name,
                    mutant.func_name,
                    args_text,
                    mutant.function_call,
                    mutant.id,
                ),
            )
