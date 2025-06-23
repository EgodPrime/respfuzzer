import json
from typing import List, Optional
from mplfuzz.db.base import get_db_cursor
from mplfuzz.utils.result import Err, Ok, Result, resultify
from mplfuzz.models import Solution, Argument, ArgumentExpr


# 创建数据库表
with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS solution (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_id INTEGER,
            library_name TEXT,
            api_name TEXT,
            args TEXT,
            arg_exprs TEXT,
            apicall_expr TEXT
        )"""
    )


@resultify
def create_solution(solution: Solution) -> Result[int, Exception]:
    """
    将 Solution 插入到数据库中，并返回新生成的 ID。
    """
    # 将 args 和 arg_exprs 转为 JSON 字符串
    args_text = json.dumps([arg.model_dump() for arg in solution.args])
    arg_exprs_text = json.dumps([expr.model_dump() for expr in solution.arg_exprs])
    apicall_expr = solution.apicall_expr

    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO solution (
                api_id,
                library_name, 
                api_name, 
                args, 
                arg_exprs, 
                apicall_expr
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (
                solution.api_id,
                solution.library_name,
                solution.api_name,
                args_text,
                arg_exprs_text,
                apicall_expr,
            ),
        )
        solution_id = cur.lastrowid
        return Ok(solution_id)

@resultify
def create_solutions(solutions: list[Solution]) -> Result[list[int], Exception]:
    res = [create_solution(solution).unwrap() for solution in solutions]
    return res

@resultify
def get_solution(solution_id: int) -> Result[Optional[Solution], Exception]:
    """
    根据 ID 获取一个 Solution。
    """
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM solution WHERE id = ?", (solution_id,))
        row = cur.fetchone()
        if not row:
            return Ok(None)

        # 解析 JSON 字符串回模型
        args = [Argument(**arg) for arg in json.loads(row[4])]
        arg_exprs = [ArgumentExpr(**expr) for expr in json.loads(row[5])]

        solution = Solution(
            id=row[0],
            api_id=row[1],
            library_name=row[2],
            api_name=row[3],
            args=args,
            arg_exprs=arg_exprs,
            apicall_expr=row[6],
        )
        return Ok(solution)

@resultify
def get_solution_by_api_id(api_id: int) -> Result[Optional[Solution], Exception]:
    with get_db_cursor() as cur:
        cur.execute("SELECT * FROM solution WHERE api_id = ?", (api_id,))
        row = cur.fetchone()
        if not row:
            return Ok(None)
        args = [Argument(**arg) for arg in json.loads(row[4])]
        arg_exprs = [ArgumentExpr(**expr) for expr in json.loads(row[5])]
        solution = Solution(
            id=row[0],
            api_id=row[1],
            library_name=row[2],
            api_name=row[3],
            args=args,
            arg_exprs=arg_exprs,
            apicall_expr=row[6],
        )
        return Ok(solution)

@resultify
def get_solutions(library_name: Optional[str] = None, api_name: Optional[str] = None) -> Result[List[Solution], Exception]:
    """
    获取多个 Solution，支持按 library_name 和 api_name 过滤。
    """
    filter_conditions = []
    params = []

    if library_name:
        filter_conditions.append("library_name = ?")
        params.append(library_name)

    if api_name:
        filter_conditions.append("api_name = ?")
        params.append(api_name)

    where_clause = "WHERE " + " AND ".join(filter_conditions) if filter_conditions else ""

    with get_db_cursor() as cur:
        cur.execute(f"SELECT * FROM solution {where_clause}", tuple(params))
        rows = cur.fetchall()
        solutions = []

        for row in rows:
            args = [Argument(**arg) for arg in json.loads(row[4])]
            arg_exprs = [ArgumentExpr(**expr) for expr in json.loads(row[5])]

            solution = Solution(
                id=row[0],
                api_id=row[1],
                library_name=row[2],
                api_name=row[3],
                args=args,
                arg_exprs=arg_exprs,
                apicall_expr=row[6],
            )
            solutions.append(solution)

        return Ok(solutions)