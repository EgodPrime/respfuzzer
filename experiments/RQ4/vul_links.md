# RQ4 漏洞复现脚本 ↔ Issue 链接

本目录保留四个漏洞复现脚本；每个脚本在未打补丁的库版本上 `uv run python vul_<n>_<lib>.py`
即可复现故障行为（`vul_3_scipy.py` 尾部 docstring 记录了实测的库版本与 Python 版本；
`vul_4_cpython.py` 无输出、以「不报错」本身复现 CPython 与 PyPy 的行为差异）。
本文件记录脚本与上游 issue 的一一对应。
状态快照日期：**2026-11-16**（GitHub 页面 + timeline API 采集；证据素材与维护者评估原文见
`docs/plan/③实验重做/真实漏洞案例证据（表X）.md`）。

| 脚本 | 库 / 版本 | Issue | 标题 | 状态（快照日） |
|---|---|---|---|---|
| `vul_1_nltk.py` | NLTK 3.9.2 / Python 3.13 | [nltk/nltk#3441](https://github.com/nltk/nltk/issues/3441) | `elementtree_indent` 应在文档中限制 `level` 参数范围 | **open**（无标签） |
| `vul_2_nltk.py` | NLTK 3.9.2 / Python 3.13 | [nltk/nltk#3444](https://github.com/nltk/nltk/issues/3444) | `sentence_nist` 对非法 `n` 值未优雅处理（`ZeroDivisionError`） | **open**（无标签） |
| `vul_3_scipy.py` | SciPy 1.16.2 / NumPy 2.3.4 / Python 3.13.9 | [scipy/scipy#23973](https://github.com/scipy/scipy/issues/23973) | `obl_cv_seq(1,3,2.0)` 返回结果后崩溃（`free(): invalid size`，堆越界写） | **closed as duplicate**（2025-11-13，dup of [#22316](https://github.com/scipy/scipy/issues/22316)，根 issue 仍 open） |
| `vul_4_cpython.py` | CPython 3.13 / Python 3.13.15 / Linux | [python/cpython#142968](https://github.com/python/cpython/issues/142968) | `token.ISNONTERMINAL/ISTERMINAL` 接受 float（CPython）而 PyPy 抛 `TypeError` | **closed as not planned**（2026-01-04） |

复现要点：

- `vul_1_nltk.py`：`nltk.elementtree_indent(root, level=1612054756)` —— `level` 无上界，
  深递归/字符串膨胀可致 CPU 过载或进程被杀。
- `vul_2_nltk.py`：`sentence_nist(refs, hyp, 19)` 与 `n=0` 均触发 `ZeroDivisionError`
  （`n` 缺范围校验；`n=18` 为合法上界）。
- `vul_3_scipy.py`：`obl_cv_seq(1, 3, 2.0)` 先打印结果，随后 `free(): invalid size` 进程 abort；
  修复 PR [scipy#74](https://github.com/scipy/scipy/pull/74) closed 未合并。
- `vul_4_cpython.py`：`ISNONTERMINAL(0.5)` / `ISTERMINAL(0.5)`——CPython 不报错（exit 0），
  PyPy 对同类 float 实参抛 `TypeError`；行为一致性报告，2026-01-04 以 not planned 关闭。
四个 issue（`vul_1_nltk.py` / `vul_2_nltk.py` / `vul_3_scipy.py` / `vul_4_cpython.py`
各自对应上表 issue）均由 RespFuzzer 战役产出、同一报告人账号（EgodPrime）提交。
等预算基线对照结论：四个 issue 在同预算下均未被基线发现。
