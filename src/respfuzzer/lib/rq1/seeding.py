"""RQ1 F2：run 级随机种子注入与记录。

run 间变异有两个正交来源：
  1. LLM 采样（temperature=0.7，RQ1 F4）——候选多样性的主来源；
  2. run 级随机种子（本模块）——随机化函数处理顺序/批次分片，
     固定 seed 则顺序确定为可重放（run 分布级可复现，I25/E17）。

实现要点：使用独立 ``random.Random(seed)`` 实例，绝不污染全局 random；
洗牌作用于副本，保证「只换顺序、不改函数集合本身」的集合不变量。
"""

from __future__ import annotations

import random


def seed_run(seed: int) -> random.Random:
  """由 run 种子构造独立 RNG（与全局 random 隔离，保证可复现）。"""
  return random.Random(seed)


def shuffled(functions: list, rng: random.Random) -> list:
  """确定性洗牌副本：返回新列表，入参不被修改，集合不变（ut-F2-1/4）。

  相同 ``rng``（同 seed）对相同输入产生相同顺序（ut-F2-1），
  不同 seed 产生不同顺序（ut-F2-2）。
  """
  items = list(functions)
  rng.shuffle(items)
  return items


def derive_run_seed(base_seed: int, run_index: int) -> int:
  """按 run 序次派生稳定种子（1..runs），同 base+run 恒同值（可复现）。"""
  return (base_seed * 1000003 + run_index) % (2**31 - 1)
