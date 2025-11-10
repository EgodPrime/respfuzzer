"""
该模块提供快速调用大型语言模型（LLM）API的实用函数。
节省掉客户端创建、参数设置等重复工作。
"""

import openai
from tracefuzz.utils.config import get_config

llm_cfg = get_config("llm")
BASE_URL = llm_cfg.get("base_url")
API_KEY = llm_cfg.get("api_key")
MODEL_NAME = llm_cfg.get("model_name")
TEMPERATURE = llm_cfg.get("temperature", 0.7)
TOP_P = llm_cfg.get("top_p", 0.8)
TOP_K = llm_cfg.get("top_k", 20)
PRESENCE_PENALTY = llm_cfg.get("presence_penalty", 1.0)

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)

def _chat(messages, **kwargs) -> str:
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=messages,
        stream=False,
        **kwargs,
    )
    return response.choices[0].message.content

def chat(messages, **kwargs) -> str:
    if "temperature" not in kwargs:
        kwargs["temperature"] = TEMPERATURE
    if "top_p" not in kwargs:
        kwargs["top_p"] = TOP_P
    if "top_k" not in kwargs:
        kwargs["top_k"] = TOP_K
    if "presence_penalty" not in kwargs:
        kwargs["presence_penalty"] = PRESENCE_PENALTY
    return _chat(messages, **kwargs)

def query(prompt: str, **kwargs) -> str:
    messages = [{"role": "user", "content": prompt}]
    return chat(messages, **kwargs)

