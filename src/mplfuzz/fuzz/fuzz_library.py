import argparse
import ctypes
import importlib
import importlib.util
import io
import os
import sys
import time
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
    get_all_api_cals,
    get_or_create_db,
    init_fuzz,
    start_fuzz,
)
from repfuzz.fuzz import fuzz_api
from repfuzz.fuzz.static_instrument import instrument_module


def safe_fuzz(
    library_name: str, queue: Queue, current_api, c_conn: Connection, black_set: set
) -> None:
    """
    Safely fuzz the given library by executing the fuzzing process in a separate process.

    This function is designed to handle the fuzzing process in a safe manner by:
    - Instrumenting the library using the `plfuzz.fuzz.static_instrument` module to add fuzzing functionality.

    Args:
        library_name (str): The name of the library to be fuzzed.
        queue (Queue): A multiprocessing queue to receive API calls.
        current_api: The current API being fuzzed.
        c_conn (Connection): A connection to the parent process for communication.
        black_set (set): A set of blacklisted API calls.

    Returns:
        None
    """
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
            logger.info(f"Execute {full_name}")
            # Save the API call to a file for later analysis
            with open("/tmp/api_call.py", "w") as f:
                f.write(api_call)
            exec(api_call)

        c_conn.send(1)  # Signal to the parent process that fuzzing is complete.


def fuzz_one_library(library_name: str) -> None:
    """
    The main entry point for the fuzzing process. One library is fuzzed at a time.

    This function initializes the database, sets up the queue, and starts the safe fuzzing process.

    Args:
        library_name (str): The name of the library to be fuzzed.

    Returns:
        None
    """
    conn = get_or_create_db(library_name)
    total_rows = get_all_api_cals(conn)

    queue = Queue()

    # Populate the queue with API calls that are not in the blacklist.
    for full_name, api_call in total_rows:
        if full_name in blacklist.get(library_name, []):
            continue
        queue.put((full_name, api_call))

    logger.info(
        f"There are {len(total_rows)} api calls for {library_name} to fuzz."
    )

    # create some shared memory structures for inter-process communication.
    exec_counter = 0
    exec_status = defaultdict(int)
    manager = Manager()
    current_api = manager.Value(ctypes.c_char_p, "")
    black_set = set()
    p_conn, c_conn = Pipe()

    dcov.open_bitmap_py()
    dcov.clear_bitmap_py()

    t0 = time.time()
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
                    f"\n{Fore.RED}Fuzzing process timeout, restarting...",
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

    dt = time.time() - t0
    add_fuzz_record(conn, int(time.time()), exec_counter, dt, dcov.count_bits_py())
    dcov.close_bitmap_py()

def main():
    argparser = argparse.ArgumentParser(description="Fuzzing library")
    argparser.add_argument(
        "-l", "--library_name", type=str, help="Library name to be fuzzeded", required=False
    )
    args = argparser.parse_args()

    fake_stdout = io.StringIO()
    fake_stderr = io.StringIO()
    sys.stdout = fake_stdout
    sys.stderr = fake_stderr
    if args.library_name:
        library_name = args.library_name
        fuzz_one_library(library_name)
    else:
        for target in tgts:
            if target in skip:
                logger.info(f"skip {target}")
                continue
            fuzz_one_library(target)

if __name__ == "__main__":
    main()