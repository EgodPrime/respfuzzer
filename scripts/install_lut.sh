# !/bin/bash

USE_UV=1

if [ "$USE_UV" -eq 1 ]; then
    uv="uv"
else
    uv=""
fi

uv pip install simplejson nltk dask pyyaml numpy pandas scikit-learn scipy requests spacy prophet plotly
uv pip install torch -i https://download.pytorch.org/whl/cpu
uv pip install paddlepaddle -i https://www.paddlepaddle.org.cn/packages/stable/cpu/

