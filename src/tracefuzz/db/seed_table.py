import json
from typing import Iterator, List, Optional

from tracefuzz.db.base import get_db_cursor
from tracefuzz.models import Argument, Seed

# 创建数据库表
with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS seed (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            func_id INTEGER,
            library_name TEXT,
            func_name TEXT,
            args TEXT,
            function_call TEXT
        )"""
    )


def create_seed(seed: Seed) -> int:
    """
    将 Seed 插入到数据库中，并返回新生成的 ID。
    """
    # 将 args 和 arg_exprs 转为 JSON 字符串
    args_text = json.dumps([arg.model_dump() for arg in seed.args])

    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO seed (
                func_id,
                library_name, 
                func_name, 
                args, 
                function_call
            ) VALUES (?, ?, ?, ?, ?)""",
            (
                seed.func_id,
                seed.library_name,
                seed.func_name,
                args_text,
                seed.function_call,
            ),
        )
        return cur.lastrowid


def create_seeds(seeds: list[Seed]) -> list[int]:
    res = [create_seed(seed) for seed in seeds]
    return res


def get_seed(seed_id: int) -> Optional[Seed]:
    """
    根据 ID 获取一个 Solution。
    """
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM seed WHERE id = ?", (seed_id,))
        row = cur.fetchone()
        if not row:
            return None

        # 解析 JSON 字符串回模型
        args = [Argument(**arg) for arg in json.loads(row[4])]

        seed = Seed(
            id=row[0],
            func_id=row[1],
            library_name=row[2],
            func_name=row[3],
            args=args,
            function_call=row[5],
        )
        return seed


def get_seed_by_function_id(func_id: int) -> Optional[Seed]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM seed WHERE func_id = ?", (func_id,))
        row = cur.fetchone()
        if not row:
            return None
        args = [Argument(**arg) for arg in json.loads(row[4])]
        seed = Seed(
            id=row[0],
            func_id=row[1],
            library_name=row[2],
            func_name=row[3],
            args=args,
            function_call=row[5],
        )
        return seed


def get_seeds(
    library_name: Optional[str] = None, func_name: Optional[str] = None
) -> List[Seed]:
    """
    获取多个 Solution，支持按 library_name 和 func_name 过滤。
    """
    filter_conditions = []
    params = []

    if library_name:
        filter_conditions.append("library_name = ?")
        params.append(library_name)

    if func_name:
        filter_conditions.append("func_name = ?")
        params.append(func_name)

    where_clause = (
        "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""
    )

    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM seed {where_clause}", tuple(params))
        rows = cur.fetchall()
        seeds = []

        for row in rows:
            args = [Argument(**arg) for arg in json.loads(row[4])]

            seed = Seed(
                id=row[0],
                func_id=row[1],
                library_name=row[2],
                func_name=row[3],
                args=args,
                function_call=row[5],
            )
            seeds.append(seed)

        return seeds


def get_seeds_iter(
    library_name: Optional[str] = None, func_name: Optional[str] = None
) -> Iterator[Seed]:
    """
    获取多个 Seed 的迭代器，支持按 library_name 和 func_name 过滤。
    """
    filter_conditions = []
    params = []

    if library_name:
        filter_conditions.append("library_name = ?")
        params.append(library_name)

    if func_name:
        filter_conditions.append("func_name = ?")
        params.append(func_name)

    where_clause = (
        "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""
    )

    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM seed {where_clause}", tuple(params))
        while True:
            row = cur.fetchone()
            if not row:
                break
            args = [Argument(**arg) for arg in json.loads(row[4])]

            seed = Seed(
                id=row[0],
                func_id=row[1],
                library_name=row[2],
                func_name=row[3],
                args=args,
                function_call=row[5],
            )
            yield seed
