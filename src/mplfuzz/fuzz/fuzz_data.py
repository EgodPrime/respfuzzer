import argparse
import ctypes
import importlib
import importlib.util
import io
import json
import os
import sys
import time
import traceback
from loguru import logger
from pathlib import Path
from collections import defaultdict
from multiprocessing import Manager, Pipe, Process, Queue
from multiprocessing.connection import Connection
import dcov

from colorama import Fore

from repfuzz.config import FUZZ, blacklist, skip, tgts
from repfuzz.database.sqlite_proxy import (
    add_fuzz_record,
    query_api_call_by_full_name,
    get_or_create_db,
    init_fuzz,
    start_fuzz,
)
from repfuzz.fuzz import fuzz_api
from repfuzz.fuzz.static_instrument import instrument_module


# def safe_fuzz(
#     library_names: list[str], queue: Queue, current_api, c_conn: Connection, black_set: set
# ) -> None:

#     logger.info(f"Fuzzing process started")

#     setattr(fuzz_api, "current_api", current_api)
#     setattr(fuzz_api, "c_conn", c_conn)
#     setattr(fuzz_api, "black_set", black_set)

#     sources = []
#     for library_name in library_names:
#         spec = importlib.util.find_spec(library_name)
#         origin = spec.origin
#         assert origin is not None, f"Could not find the origin of the library {library_name}"
#         sources.append(origin)

#     with dcov.LoaderWrapper() as l:
#         for source in sources:
#             l.add_source(source)
#         for library_name in library_names:
#             target = importlib.import_module(library_name)
#             instrument_module(target)

#         logger.debug(f"init coverage: {dcov.count_bits_py()}")

#         init_fuzz()
#         start_fuzz()

#         while not queue.empty():
#             full_name, api_call = queue.get()
#             logger.info(f"Execute {full_name}")
#             # Save the API call to a file for later analysis
#             with open("/tmp/api_call.py", "w") as f:
#                 f.write(api_call)
#             exec(api_call)

#         c_conn.send(1)  # Signal to the parent process that fuzzing is complete.

def safe_fuzz(
    library_name: str, queue: Queue, current_api, c_conn: Connection, black_set: set
) -> None:

    logger.info(f"Fuzzing process started for {library_name}")

    setattr(fuzz_api, "current_api", current_api)
    setattr(fuzz_api, "c_conn", c_conn)
    setattr(fuzz_api, "black_set", black_set)

    spec = importlib.util.find_spec(library_name)
    origin = spec.origin
    assert origin is not None, f"Could not find the origin of the library {library_name}"

    with dcov.LoaderWrapper() as l:
        l.add_source(origin)
        target = importlib.import_module(library_name)
        instrument_module(target)

        init_fuzz()
        start_fuzz()

        while not queue.empty():
            full_name, api_call = queue.get()
            # Save the API call to a file for later analysis
            with open("/tmp/api_call.py", "w") as f:
                f.write(api_call)
            exec(api_call)

        c_conn.send(1)  # Signal to the parent process that fuzzing is complete.

def skip_imports_coverage(imports_file_path:str, white_list: list[str]):
    # Run this function after initializing dcov and before starting fuzzing.
    import re
    re_str = r"(?:from ([a-zA-Z_][a-zA-Z_\.0-9]+) import [a-zA-Z_][a-zA-Z_0-9\*,\s]+(?:\s*#.*)?)|(?:import ([a-zA-Z_][a-zA-Z_\.0-9,\s]+)(?:\s*#.*)?)"
    imports_data = {}
    with open(imports_file_path, "r") as file:
        for line in file.readlines():
            line = line.strip()
            match = re.search(re_str, line)
            if match:
                mod = match.group(1).strip() if match.group(1) else match.group(2).strip() if match.group(2) else ''
                if '.' in mod:
                    mod = mod.split('.')[0]
                if mod in white_list:
                    if mod not in imports_data:
                        imports_data[mod] = []
                    imports_data[mod].append(line)
                    # logger.debug(f"Found a import for {mod}: `{line}`")
    for library_name, imports in imports_data.items():
        spec = importlib.util.find_spec(library_name)
        origin = spec.origin
        assert origin is not None, f"Could not find the origin of the library {library_name}"
        with dcov.LoaderWrapper() as l:
            l.add_source(origin)
            for im in imports:
                try:
                    exec(im)
                except:
                    pass

    logger.info(f"Initial coverage is {dcov.count_bits_py()}")

def fuzz_dataset(dataset: dict) -> None:
    
    total_rows = 0
    dcov.open_bitmap_py() 
    dcov.clear_bitmap_py()
    p = Process(target=skip_imports_coverage, args=('/root/repfuzz/experiments/RQ4/cleaned_import_list_1000.txt', dataset.keys()))
    p.start()
    p.join()
    # skip_imports_coverage('/root/repfuzz/experiments/RQ4/cleaned_import_list.txt', dataset.keys())

    for library_name, api_infos in dataset.items():
        queue = Queue()
        conn = get_or_create_db(library_name)
        logger.debug(f"library {library_name} has {len(api_infos)} api to be fuzzed")

        for api_name in api_infos.keys():
            full_name = f"{library_name}.{api_name}"
            api_calls = query_api_call_by_full_name(conn, full_name)
            total_rows += len(api_calls)
            # logger.debug(f"{full_name} has {len(api_calls)} api calls")
            queue.put((full_name, f"import {library_name}"))
            total_rows += 1
            for api_call in api_calls:
                queue.put((full_name, api_call))
    

            # create some shared memory structures for inter-process communication.
            exec_counter = 0
            exec_status = defaultdict(int)
            manager = Manager()
            current_api = manager.Value(ctypes.c_char_p, "")
            black_set = set()
            p_conn, c_conn = Pipe()

            
            logger.info(f"Fuzz {full_name} start")
            while not queue.empty():
                # Start a new worker process to execute all the API calls.
                
                safe_worker = Process(
                    target=safe_fuzz,
                    args=(library_name, queue, current_api, c_conn, black_set),
                )
                safe_worker.start()

                while True:
                    p_conn.send(
                        1
                    )  # send a signal to the worker process to allow it to execute the next execution.
                    if p_conn.poll(
                        10
                    ):  # check if the worker process has finished the current execution within the timeout.
                        r = p_conn.recv()
                        if r:  # receive 1 if the worker process has finished all the API calls.
                            logger.info(f"Fuzzing {library_name} done")
                            break
                        else:  # receive 0 if the worker process has finished the current execution but not all the API calls.
                            exec_counter += 1
                            exec_status[current_api.value] += 1
                    else:  # if the worker process has not finished the current execution within the timeout.
                        logger.info(
                            f"\nFuzzing process timeout, restarting...",
                            file=sys.__stderr__,
                        )

                        """
                        if the worker process has not finished the current execution within the timeout and
                        the total number of executions for the current API call is less than 10,
                        add the current API call to the black_set and kill the worker process.
                        """
                        if exec_status[current_api.value] < 10:
                            black_set.add(current_api.value)
                        os.kill(safe_worker.pid, 9)

                        # save the triggering code and API call to a file.
                        save_dir = FUZZ.potential_bugs
                        save_dir.mkdir(parents=True, exist_ok=True)
                        filelist = os.listdir(save_dir)
                        idx = len(filelist) + 1
                        filepath = save_dir.joinpath(f"{idx}.py")
                        with open(filepath, "w") as f:
                            f.write("# Corresponding API call\n")
                            f.write(open("/tmp/api_call.py", "r").read())
                            f.write("# Triggering Code\n")
                            f.write(open("/tmp/fuzz.py", "r").read())
                        break  # break the current fuzzing loop and restart the process.
                safe_worker.join()
                
            logger.info(f"Fuzz {full_name} done")
            logger.info(f"Coverage now: {dcov.count_bits_py()}")

    logger.info(f"final total coverage: {dcov.count_bits_py()}")
    dcov.close_bitmap_py()

def main():
    argparser = argparse.ArgumentParser(description="Fuzzing dataset")
    argparser.add_argument(
        "-d", "--dataset_path", type=str, help="Path to the dataset directory", required=False
    )
    args = argparser.parse_args()

    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr

    data = json.load(open(args.dataset_path, "r"))
    fuzz_dataset(data)

if __name__ == "__main__":
    main()