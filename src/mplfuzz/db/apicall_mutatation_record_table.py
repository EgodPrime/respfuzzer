from typing import List, Optional
from mplfuzz.db.base import get_db_cursor
from mplfuzz.utils.result import Err, Ok, Result, resultify
from mplfuzz.models import Mutant


# 创建表
with get_db_cursor() as cur:
    cur.execute(
        """CREATE TABLE IF NOT EXISTS mutant (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            solution_id TEXT,
            library_name TEXT,
            api_name TEXT,
            apicall_expr_ori TEXT,
            apicall_expr_new TEXT
        )"""
    )


@resultify
def create_mutant(mutant: Mutant) -> Result[None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(
            """INSERT INTO mutant 
               (solution_id, library_name, api_name, apicall_expr_ori, apicall_expr_new) 
               VALUES (?, ?, ?, ?, ?)""",
            (
                mutant.solution_id,
                mutant.library_name,
                mutant.api_name,
                mutant.apicall_expr_ori,
                mutant.apicall_expr_new,
            ),
        )
    return Ok(None)


@resultify
def get_mutant(mutant_id: str) -> Result[Mutant | None, Exception]:
    with get_db_cursor() as cur:
        cur.execute(
            "SELECT * FROM mutant WHERE mutant_id = ?", (mutant_id,)
        )
        row = cur.fetchone()
        if row:
            return Mutant(
                solution_id=row[0],
                library_name=row[1],
                api_name=row[2],
                apicall_expr_ori=row[3],
                apicall_expr_new=row[4],
            )
        else:
            return None


@resultify
def get_mutants(solution_id: Optional[str] = None) -> Result[List[Mutant], Exception]:
    with get_db_cursor() as cur:
        if solution_id:
            cur.execute(
                "SELECT * FROM mutant WHERE solution_id = ?", (solution_id,)
            )
        else:
            cur.execute("SELECT * FROM mutant")
        rows = cur.fetchall()
        mutants = [
            Mutant(
                solution_id=row[0],
                library_name=row[1],
                api_name=row[2],
                apicall_expr_ori=row[3],
                apicall_expr_new=row[4],
            )
            for row in rows
        ]
    return Ok(mutants)
