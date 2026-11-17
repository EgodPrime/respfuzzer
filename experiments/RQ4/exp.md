# RQ4 Experiment

RQ4 reports the real-world vulnerabilities discovered by RespFuzzer
campaigns. Crash candidates are mined from the RQ1–RQ3 fuzzing logs and
validated end to end. All scripts live in `experiments/RQ4/` and write their
intermediates as JSON next to the scripts:

1. `summary_from_logs.py <log dir>` — scans fuzzing logs for crash/timeout
   records (`Mutant N execution timeout … Last random state`) and writes
   `crash_summary.json`;
2. `simple_replay.py` — replays each crash and synthesises a PoC with the LLM
   → `replay_results.json`;
3. `filter.py` — an LLM judge removes false positives, keeping genuine
   crashes and potential security issues → `filtered_replay_results.json`;
4. `final_validate.py` — re-executes every filtered candidate for final
   confirmation.

The confirmed findings are the four vulnerabilities `vul_1_nltk.py`,
`vul_2_nltk.py`, `vul_3_scipy.py` and `vul_4_torch.py`. Each file is a
standalone reproduction script for one reported issue: run it with `uv run` to
reproduce the faulty behaviour on the unpatched library version.
