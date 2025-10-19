import json
import random

from tracefuzz.models import Seed
from tracefuzz.repos.seed_table import get_seeds_iter


def sample_apis(json_data: dict, total_samples: int = 100):
    # 统计每个库的API数量
    api_counts = {lib: len(apis) for lib, apis in json_data.items()}

    # 计算总API数量
    total_apis = sum(api_counts.values())

    # 计算每个库的采样数量
    sample_counts = {}
    remaining_samples = total_samples
    for lib, count in api_counts.items():
        # 按比例计算采样数量，至少采样1个
        samples = max(1, int((count / total_apis) * total_samples))
        sample_counts[lib] = samples
        remaining_samples -= samples

    # 确保总采样数量为100，调整采样数量
    while remaining_samples > 0:
        for lib in api_counts:
            if remaining_samples > 0 and sample_counts[lib] < api_counts[lib]:
                sample_counts[lib] += 1
                remaining_samples -= 1

    # 从每个库中随机采样API
    sampled_apis = {}
    for lib, apis in json_data.items():
        sample = random.sample(list(apis.items()), sample_counts[lib])
        sampled_apis[lib] = {}
        for k, v in sample:
            sampled_apis[lib][k] = v

    return sampled_apis


def save_to_data(seed: Seed, data: dict):
    # tokens = row[0].split('.')
    library_name, api_name = seed.func_name.split(".", 1)
    n = len(seed.args)
    v = {"pn": [n, n]}
    if library_name not in data:
        data[library_name] = {}
    data[library_name][api_name] = v


def sample_dyfuzz_format(n_samples: int = 200):
    """Sample `n_samples` seeds from the database and export to DyFuzz format JSON."""
    data = {}
    for seed in get_seeds_iter():
        save_to_data(seed, data)
    output_file_name = "tracefuzz_seeds.json"
    sampled_data = sample_apis(data, total_samples=n_samples)
    json.dump(sampled_data, open(output_file_name, "w"), indent=2)
