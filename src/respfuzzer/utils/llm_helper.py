"""
该模块提供快速调用大型语言模型（LLM）API的实用函数。
节省掉客户端创建、参数设置等重复工作。
"""

import openai
from langchain_openai import ChatOpenAI
from respfuzzer.utils.config import get_config

llm_cfg = get_config("llm")
BASE_URL = llm_cfg.get("base_url")
API_KEY = llm_cfg.get("api_key")
MODEL_NAME = llm_cfg.get("model_name")
TEMPERATURE = llm_cfg.get("temperature", 0.7)
MAX_TOKENS = llm_cfg.get("max_tokens", 32768)
TIMEOUT = float(llm_cfg.get("timeout", 30))

client = openai.OpenAI(api_key=API_KEY, base_url=BASE_URL)


class SimpleLLMClient:
    def __init__(self, **cfg):
        self.client = openai.OpenAI(
            api_key=cfg.get("api_key"), base_url=cfg.get("base_url")
        )
        self.model_name = cfg.get("model_name")
        self.temperature = cfg.get("temperature", 0.7)

    def _chat(self, messages, **kwargs) -> str:
        response = self.client.chat.completions.create(
            model=self.model_name,
            messages=messages,
            stream=False,
            **kwargs,
            extra_body={"chat_template_kwargs": {"enable_thinking": False},},
        )
        return response.choices[0].message.content

    def chat(self, messages, **kwargs) -> str:
        if "temperature" not in kwargs:
            kwargs["temperature"] = self.temperature
        return self._chat(messages, **kwargs)

    def query(self, prompt: str, **kwargs) -> str:
        messages = [{"role": "user", "content": prompt}]
        return self.chat(messages, **kwargs)


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
    return _chat(messages, **kwargs)


# ==================== 单例存储 ====================
_sys_llm: ChatOpenAI | None = None

def get_sys_llm() -> ChatOpenAI:
    """获取全局单例的 ChatOpenAI 实例"""
    global _sys_llm
    if _sys_llm is None:
        _sys_llm = ChatOpenAI(
            model_name=MODEL_NAME,
            temperature=TEMPERATURE,
            max_tokens=MAX_TOKENS,
            openai_api_key=API_KEY,
            base_url=BASE_URL,
            timeout=TIMEOUT,
            extra_body={"chat_template_kwargs": {"enable_thinking": False},},
        )
    return _sys_llm