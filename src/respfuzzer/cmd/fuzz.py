import sys

import fire

from respfuzzer.lib.fuzz.fuzz_exp import fuzz_dataset, fuzz_one_library
from respfuzzer.utils.budget import normalize_budget_args


def main():
    # Fire 只认 --long-param，单横线长参数（-lrequest/-ltime）会被静默丢弃，
    # 这里把实验约定写法重写成 Fire 标准写法后再交给 Fire 解析。
    sys.argv[1:] = normalize_budget_args(sys.argv[1:])
    fire.Fire(
        {
            "fuzz_dataset": fuzz_dataset,
            "fuzz_library": fuzz_one_library,
        }
    )


if __name__ == "__main__":
    main()
