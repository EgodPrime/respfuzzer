"""共享子进程执行串行锁（exec_lock）。

设计约束：LLM 请求保持并发，但**子进程代码执行一次只允许一个并行**。
多线程并发时，若多个线程同时 fork 大库子进程（torch/pandas/paddle 各 ~1-2GB），
瞬间内存峰值远超 15GB → OOM 或段错误（SIGSEGV，torch ours 并发 16 教训）。

RQ1Executor（对照解法）与 agentic QueitExecutor（ours solve）是两个独立执行器，
但都 fork 大库子进程，故共享同一把锁。

用法：子进程 Popen→communicate→cleanup 整段包 `with EXEC_LOCK:`。
无锁保护的是 LLM 在线请求（openai client 线程安全，可并发）。
"""
"""共享子进程执行并发控制（exec_lock）.

两把闸：
- ``EXEC_LOCK``：threading.Lock——fork 瞬间互斥（copy-on-write 页表复制峰值叠加
  是 OOM 触发点之一）；RQ1 短命子进程整段（Popen→communicate→cleanup）包它。
- ``EXEC_SEM``：BoundedSemaphore——常驻执行子进程（RQ3/RQ4 fuzz worker）的
  同时存活数上限。多线程并发 fork 大库子进程（torch/paddle 各 ~1-2GB）
  会使内存峰值远超 15GB → OOM/段错误（torch ours 并发 16 教训）。
  fuzz 场景完全串行会使 ER-21 时间成本不可接受，故限并发而非串行：
  默认 4（4×2GB=8GB<15GB），可用 ``RESPFUZZER_EXEC_CONCURRENCY`` 覆盖。

无锁保护的是 LLM 在线请求（openai client 线程安全，保持并发）。
"""
import os
import threading

EXEC_LOCK = threading.Lock()
EXEC_SEM = threading.BoundedSemaphore(
    max(1, int(os.environ.get("RESPFUZZER_EXEC_CONCURRENCY", "4")))
)