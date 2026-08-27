import os
from datetime import datetime
from enum import IntEnum


class LEVEL(IntEnum):
    INFO = 1  # most important message (are bugs found?, overview, etc)
    TRACE = 2
    VERBOSE = 3  # most verbose messages (including validation messages, etc)

    # logging structure breakdown into validation, and sample generation,
    # and any potential bugs are logged always in main log.txt
    # TODO: support logging levels


class Logger:
    def __init__(self, basedir, file_name: str, level: LEVEL = LEVEL.INFO):
        self.logfile = os.path.join(basedir, file_name)
        self.level = level
        os.makedirs(basedir, exist_ok=True)

    @staticmethod
    def format_log(msg, level: LEVEL = LEVEL.VERBOSE):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[
            :-3
        ]  # accurate to milliseconds
        return f"{timestamp} | {level.name:<8} | {msg}"

    def log(self, msg, level: LEVEL = LEVEL.VERBOSE):

        formatted_msg = self.format_log(msg, level)
        with open(self.logfile, "a+", encoding="utf-8") as logfile:
            logfile.write(formatted_msg + "\n")

    def logo(self, msg, level: LEVEL = LEVEL.VERBOSE):
        try:
            with open(self.logfile, "a+", encoding="utf-8") as logfile:
                logfile.write(self.format_log(msg, level))
                logfile.write("\n")
            if level <= self.level:
                print(self.format_log(msg, level))
        except Exception as e:
            pass

    def info(self, msg):
        self.log(msg, LEVEL.INFO)
