#!/bin/bash
# Fixed library versions for reproducibility (synced from `uv pip list`, 2026-10-23)
# Libraries under test (6): nltk numpy pandas scikit-learn scipy spacy
# Usage: bash scripts/install_lut.sh

set -e

uv pip install \
    nltk==3.10.3 \
    numpy==2.5.4 \
    pandas==2.3.3 \
    scikit-learn==1.8.0 \
    scipy==1.16.3 \
    spacy==3.8.11
