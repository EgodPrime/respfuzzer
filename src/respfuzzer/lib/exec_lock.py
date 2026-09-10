"""共享子进程执行串行锁（exec_lock）。

设计约束：LLM 请求保持并发，但**子进程代码执行一次只允许一个并行**。
多线程并发时，若多个线程同时 fork 大库子进程（torch/pandas/paddle 各 ~1-2GB），
瞬间内存峰值远超 15GB → OOM 或段错误（SIGSEGV，torch ours 并发 16 教训）。

RQ1Executor（对照解法）与 agentic QueitExecutor（ours solve）是两个独立执行器，
但都 fork 大库子进程，故共享同一把锁。

用法：子进程 Popen→communicate→cleanup 整段包 `with EXEC_LOCK:`。
无锁保护的是 LLM 在线请求（openai client 线程安全，可并发）。
"""
import threading

EXEC_LOCK = threading.Lock()