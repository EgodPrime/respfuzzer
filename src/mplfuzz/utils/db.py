import json
import sqlite3
from typing import List

from mplfuzz.models import API, MCPAPI, Solution
from mplfuzz.utils.config import get_config
from mplfuzz.utils.paths import RUNDATA_DIR
from mplfuzz.utils.result import Err, Ok, Result, resultify

config = get_config("db_config").value
db_name = config.get("db_name") + ".db"
table_name = config.get("table_name")
db_path = RUNDATA_DIR.joinpath(db_name)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute(f"CREATE TABLE IF NOT EXISTS {table_name} (name TEXT PRIMARY KEY, source TEXT, args TEXT, ret_type TEXT, mcp_code TEXT, solutions TEXT)")

@resultify
def create_api(api: API) -> Result[None, str]:
    """
    Create a new record for the given API in the database.
    Skip if the API already exists.
    """
    name = api.name
    args = json.dumps([arg.model_dump() for arg in api.args])
    cur.execute(
        f"INSERT OR IGNORE INTO {table_name} (name, source, args, ret_type) VALUES (?, ?, ?, ?)",
        (
            name,
            api.source,
            args,
            api.ret_type
        ),
    )
    conn.commit()

@resultify
def save_mcp_code_to_api(api: API, mcp_code: str) -> Result[None, str]:
    """
    Save the MCP code for the given API in the database.
    """
    cur.execute(f"UPDATE {table_name} SET mcp_code = ? WHERE name = ?", (mcp_code, api.name))
    conn.commit()

@resultify
def get_all_unmcped_apis(library_name: str = None) -> Result[List[API], str]:
    filter = "WHERE mcp_code IS NULL"
    if library_name:
        filter += f" AND name LIKE '%{library_name}%'"
    cur.execute(f"SELECT name, source, args, ret_type FROM {table_name} {filter}")
    records = cur.fetchall()
    return Ok([API(name = record[0], source = record[1], args = json.loads(record[2]), ret_type = record[3]) for record in records]) if records else Ok([])

def if_api_has_solution(api_name: str) -> Result[bool, str]:
    try:
        res = cur.execute(f"SELECT solutions FROM {table_name} WHERE name = ?", (api_name,)).fetchone()
        if res:
            return Ok(bool(res[0]))
        else:
            return Ok(False)
    except Exception as e:
        return Err(f"Error checking if API {api_name} has solutions: {str(e)}")


def save_solutions_to_api(api: MCPAPI, solutions: list[Solution]) -> Result[None, str]:
    """
    Save the solutions for the given API in the database.
    If the record does not exist, it will be created.
    """
    try:
        name = api.name
        solutions_str = json.dumps([str(s) for s in solutions])
        res = cur.execute(f"SELECT * FROM {table_name} WHERE name = ?", (name,)).fetchone()
        if res:
            cur.execute(f"UPDATE {table_name} SET solutions = ? WHERE name = ?", (solutions_str, name))
        else:
            args = json.dumps([arg.model_dump() for arg in api.args])
            code = api.to_mcp_code()
            cur.execute(
                f"INSERT INTO {table_name} (name, args, mcp_code, ret_type, solutions) VALUES (?, ?, ?, ?, ?)",
                (name, args, api.ret_type, code, solutions_str),
            )
        conn.commit()
        return Ok()
    except Exception as e:
        return Err(f"Error saving solutions for API {name}: {str(e)}")


def get_all_library_names() -> Result[list[str], str]:
    """
    Returns a list of all unique library names from the database.
    """
    try:
        res = cur.execute(
            f"""
            SELECT DISTINCT substr(name, 1, instr(name, '.') - 1) 
            FROM {table_name}
        """
        ).fetchall()
        return Ok([r[0] for r in res])
    except Exception as e:
        return Err(f"Error getting all library names: {str(e)}")


def get_status_of_library(library_name: str) -> Result[dict[str, str], str]:
    """
    Returns the status of a library from the database.
    """
    try:
        res = cur.execute(
            f"""
            SELECT name, solutions
            FROM {table_name} 
            WHERE name LIKE ? AND args != '[]'
        """,
            (f"{library_name}.%",),
        ).fetchall()
        if len(res) == 0:
            return Err(f"Library {library_name} not found")
        raw1 = {r[0]: r[1] for r in res}
        for k, v in raw1.items():
            try:
                raw1[k] = json.loads(v)
            except:
                raw1[k] = []
        for k, v in raw1.items():
            raw1[k] = len(v)
        return Ok(raw1)
    except Exception as e:
        return Err(f"Error getting status of library {library_name}: {str(e)}")

def get_all_unsolved_apis(library_name:str|None = None) -> Result[list[API], str]:
    """
    Returns a list of all unsolved APIs from the database.
    """
    try:
        fields = "name, source, args, ret_type"
        filter = "args != '[]' AND solutions IS NULL"
        if library_name:
            filter += f" AND name LIKE '{library_name}.%'"
        records = cur.execute(
            f"SELECT {fields} FROM {table_name} WHERE {filter}"
        ).fetchall()

        res = []
        for r in records:
            res.append(API(name=r[0], source=r[1], args=json.loads(r[2]), ret_type=r[3]))

        return Ok(res)
    except Exception as e:
        return Err(f"Error getting unsolved APIs: {str(e)}")
        