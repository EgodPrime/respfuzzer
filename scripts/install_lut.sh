#!/bin/bash
# Fixed library versions for reproducibility (from uv pip list, 2025-05-14)
# Usage: bash scripts/install_lut.sh

set -e

uv pip install \
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

uv pip install torch==2.9.1+cpu -i https://download.pytorch.org/whl/cpu
uv pip install paddlepaddle==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
