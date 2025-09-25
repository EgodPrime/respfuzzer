import sys
from loguru import logger
import io
import fire
import importlib
from mplfuzz.fuzz.instrument import instrument_function_via_path
from mplfuzz.utils.redis_util import get_redis_client

def safe_fuzz(solution_id:int, library_name: str, func_package_path: str):
    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr
    rc = get_redis_client()
    func_expr = rc.hget("seed", f"{solution_id}")
    target = importlib.import_module(library_name)
    instrument_function_via_path(target, func_package_path)
    exec(func_expr)

def main():
    fire.Fire(safe_fuzz)