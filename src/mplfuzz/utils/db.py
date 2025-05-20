import json
import sqlite3
from typing import Optional

from mplfuzz.models import API, MCPAPI, Solution
from mplfuzz.utils.config import get_config
from mplfuzz.utils.paths import RUNDATA_DIR
from mplfuzz.utils.result import Result, Err, Ok

config = get_config("db_config").value
db_name = config.get("db_name") + ".db"
table_name = config.get("table_name")
db_path = RUNDATA_DIR.joinpath(db_name)

conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute(
    f"CREATE TABLE IF NOT EXISTS {table_name} (name TEXT PRIMARY KEY, mcp_code TEXT, solutions TEXT)"
)


def create_api(api: MCPAPI) -> Result[None, str]:
    """
    Create a new record for the given API in the database.
    Skip if the API already exists.
    """
    try:
        name = api.name
        code = api.to_mcp_code()
        cur.execute(
            f"INSERT OR IGNORE INTO {table_name} (name, mcp_code) VALUES (?, ?)",
            (
                name,
                code,
            ),
        )
        conn.commit()
        return Ok()
    except Exception as e:
        return Err(f"Error creating API {name}: {str(e)}")


def if_api_has_solution(api_name: str) -> Result[bool, str]:
    try:
        res = cur.execute(
            f"SELECT solutions FROM {table_name} WHERE name = ?", (api_name,)
        ).fetchone()
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
            cur.execute(
                f"UPDATE {table_name} SET solutions = ? WHERE name = ?", (solutions_str, name)
            )
        else:
            code = api.to_mcp_code()
            cur.execute(
                f"INSERT INTO {table_name} (name, mcp_code, solutions) VALUES (?, ?, ?)",
                (name, code, solutions_str),
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
        res = cur.execute(f"""
            SELECT DISTINCT substr(name, 1, instr(name, '.') - 1) 
            FROM {table_name}
        """).fetchall()
        return Ok([r[0] for r in res])
    except Exception as e:
        return Err(f"Error getting all library names: {str(e)}")
    
def get_status_of_library(library_name: str) -> Result[dict[str, str], str]:
    """
    Returns the status of a library from the database.
    """
    try:
        res = cur.execute(f"""
            SELECT name, solutions 
            FROM {table_name} 
            WHERE name LIKE ? 
        """, (f"{library_name}.%",)).fetchall()
        if len(res) == 0:
            return Err(f"Library {library_name} not found")
        raw1  = {r[0]: r[1] for r in res}
        for k, v in raw1.items():
            try:
                raw1[k] = json.loads(v)
            except:
                raw1[k] = []
        for k,v in raw1.items():
            raw1[k] = len(v)
        return Ok(raw1)
    except Exception as e:
        return Err(f"Error getting status of library {library_name}: {str(e)}")