"""子进程 cwd 沙箱回归：seed 相对路径产物必须落 run_data/ 沙箱并即删，
不得污染项目根（numpy.so / paddle/ 遮蔽真实包的教训）。"""

from pathlib import Path

from respfuzzer.lib.agentic_function_resolver import QueitExecutor
from respfuzzer.lib.rq1.executor import RQ1Executor
from respfuzzer.models import ExecutionResultType, Function


def _func() -> Function:
    return Function(func_path="math.sqrt", library_name="math", source="", args=[])


def test_rq1_executor_writes_go_to_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("RESPFUZZER_RUN_DATA", str(tmp_path))
    ex = RQ1Executor()
    code = (
        "from math import sqrt\n"
        "open('rq1_sandbox_probe.txt', 'w').write('x')\n"
        "r = sqrt(4.0)\n"
    )
    res = ex.execute(_func(), code)
    assert res["covered"] is True
    assert not (Path.cwd() / "rq1_sandbox_probe.txt").exists()  # 项目根零残留
    assert not list(tmp_path.glob("fz-*"))  # 沙箱即删


def test_queit_executor_writes_go_to_sandbox(tmp_path, monkeypatch):
    monkeypatch.setenv("RESPFUZZER_RUN_DATA", str(tmp_path))
    ex = QueitExecutor()
    code = "x = len('ab')\nopen('rq1_sandbox_probe.txt', 'w').write('x')"
    res = ex.execute(code, "builtins.len")
    assert res["result_type"] == ExecutionResultType.OK
    assert not (Path.cwd() / "rq1_sandbox_probe.txt").exists()  # 项目根零残留
    assert not list(tmp_path.glob("fz-*"))  # 沙箱即删
