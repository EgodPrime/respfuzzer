# LLM Deployment Reference

All three models were served locally with SGLang on a GPU server equipped with
two RTX PRO 6000 GPUs, each model listening on port 8021 behind an
OpenAI-compatible `/v1` endpoint. At any time only one model was resident;
switching models required restarting the server. The served model name was
kept identical to the model name used by the fuzzing clients. Decoding
parameters were supplied by the client on every request, so the server only
provided inference and chat-template configuration.

## Llama-3.1-8B-Instruct

```bash
sglang serve \
  --model-path models/Meta-Llama-3.1-8B-Instruct-FP8 \
  --served-model-name Llama-3.1-8B-Instruct --host 0.0.0.0 --port 8021 \
  --mem-fraction-static 0.8 \
  --attention-backend flashinfer \
  --chunked-prefill-size 2048 \
  --max-running-requests 4 --cuda-graph-max-bs-decode 4 \
  --kv-cache-dtype fp8_e4m3 \
  --disable-radix-cache --mamba-ssm-dtype bfloat16 --mamba-full-memory-ratio 0.07 --mamba-radix-cache-strategy extra_buffer_lazy
```

The 8B model runs on FP8 weights with the flashinfer attention backend, with
radix caching explicitly disabled (`--disable-radix-cache`).

## Qwen3.6-35B-A3B

```bash
sglang serve \
  --model-path models/Qwen3.6-35B-A3B-FP8 \
  --served-model-name Qwen3.6-35B-A3B --host 0.0.0.0 --port 8021 \
  --mem-fraction-static 0.62 \
  --context-length 262144 \
  --attention-backend flashinfer \
  --chunked-prefill-size 2048 \
  --max-running-requests 4 --cuda-graph-max-bs-decode 4 \
  --kv-cache-dtype fp8_e4m3 \
  --default-chat-template-kwargs '{"enable_thinking": false}' \
  --mamba-ssm-dtype bfloat16 --mamba-radix-cache-strategy extra_buffer_lazy
```

The MoE model is served on FP8 weights with a 262,144-token context. Thinking
mode is disabled at the server level through `--default-chat-template-kwargs`,
so every request returns a direct answer rather than a reasoning trace.

## gpt-oss-120b

```bash
sglang serve \
  --model-path models/gpt-oss-120b \
  --served-model-name gpt-oss-120b --host 0.0.0.0 --port 8021 \
  --mem-fraction-static 0.95 \
  --context-length 65536 \
  --chunked-prefill-size 2048 \
  --attention-backend triton --moe-runner-backend triton --enforce-disable-flashinfer-allreduce-fusion \
  --max-running-requests 4 --cuda-graph-max-bs-decode 4 \
  --kv-cache-dtype fp8_e4m3 \
  --mamba-ssm-dtype bfloat16 --mamba-radix-cache-strategy extra_buffer_lazy
```