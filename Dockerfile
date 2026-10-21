# ============================================================
# RespFuzzer 通用实验容器镜像
# ============================================================
# 适用于 respfuzzer 所有实验（RQ1、RQ2、fuzz 等）。
# 基底镜像：Ubuntu 24.04 LTS (noble)
# Python：通过 uv 创建 3.13 虚拟环境
#
# 构建：
#   docker build -t respfuzzer:latest .
#
# 运行（单次实验 — 细粒度挂载）：
#   docker run --rm \
#     -v /path/to/respfuzzer/config.toml:/workspace/config.toml \
#     -v /path/to/respfuzzer/src:/workspace/src \
#     -v /path/to/respfuzzer/experiments:/workspace/experiments \
#     -v /path/to/respfuzzer/scripts:/workspace/scripts \
#     -v /path/to/respfuzzer/tests:/workspace/tests \
#     -v /path/to/respfuzzer/data:/workspace/data \
#     -v /path/to/respfuzzer/runs:/workspace/runs \
#     -v /path/to/respfuzzer/run_data:/workspace/run_data \
#     -w /workspace respfuzzer:latest \
#     uv run python experiments/RQ1/rq1_seed.py --mode ours --library numpy --runs 1 --seed 42
#
# 运行（交互式）：
#   docker run --rm -it \
#     -v /path/to/respfuzzer/config.toml:/workspace/config.toml \
#     -v /path/to/respfuzzer/src:/workspace/src \
#     -v /path/to/respfuzzer/experiments:/workspace/experiments \
#     -v /path/to/respfuzzer/scripts:/workspace/scripts \
#     -v /path/to/respfuzzer/tests:/workspace/tests \
#     -v /path/to/respfuzzer/data:/workspace/data \
#     -v /path/to/respfuzzer/runs:/workspace/runs \
#     -v /path/to/respfuzzer/run_data:/workspace/run_data \
#     -w /workspace respfuzzer:latest bash
#
# ============================================================

# ---------- 阶段 1：构建工具链 ----------
FROM ubuntu:24.04 AS builder

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.8.5 /uv /usr/local/bin/uv

# ---------- 阶段 2：应用镜像 ----------
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        curl \
        git \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# uv
COPY --from=builder /usr/local/bin/uv /usr/local/bin/uv

WORKDIR /workspace

# 复制项目文件（用于 uv lock + install 缓存）
COPY pyproject.toml uv.lock ./

# 安装 Python 3.13（uv 自动管理）并创建虚拟环境
RUN uv python install 3.13 && \
    uv venv .venv --python 3.13 && \
    . .venv/bin/activate && \
    uv pip install -e .

# 安装被测库（install_lut.sh 中的全部库）
# 注意：uv 不允许同一条命令出现多个 --index-url，需按 index 拆成多条 RUN
RUN . .venv/bin/activate && uv pip install \
        nltk==3.9.2 \
        dask==2025.12.0 \
        PyYAML==6.0.3 \
        prophet==1.2.1 \
        numpy==2.3.4 \
        pandas==2.3.3 \
        scikit-learn==1.8.0 \
        scipy==1.16.3 \
        requests==2.33.1 \
        spacy==3.8.11

RUN . .venv/bin/activate && uv pip install \
        torch==2.9.1+cpu \
        --index-url https://download.pytorch.org/whl/cpu

RUN . .venv/bin/activate && uv pip install \
        paddlepaddle==3.2.2 \
        --index-url https://www.paddlepaddle.org.cn/packages/stable/cpu/

# 安装 dcov
RUN . .venv/bin/activate && uv pip install git+https://github.com/EgodPrime/dcov.git@python_v2

# 创建运行时目录（供挂载点使用）
RUN mkdir -p /workspace/src \
             /workspace/experiments \
             /workspace/scripts \
             /workspace/tests \
             /workspace/data \
             /workspace/runs \
             /workspace/run_data

# 默认命令
CMD ["bash"]
