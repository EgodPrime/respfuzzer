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
            api_key=cfg.get("api_key"), base_url=cfg.get("base_url"),
            timeout=cfg.get("timeout", 300),
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
        _update_statistics(response)
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
    _update_statistics(response)
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
            max_tokens=MAX_TOKENS/2,
            openai_api_key=API_KEY,
            base_url=BASE_URL,
            timeout=TIMEOUT,
            extra_body={"chat_template_kwargs": {"enable_thinking": False},},
        )
    return _sys_llm


# ==================== 指标统计 ====================
_total_llm_calls: int = 0
_total_input_tokens: int = 0
_total_output_tokens: int = 0


def get_total_llm_calls() -> int:
    """获取总LLM调用次数"""
    return _total_llm_calls


def get_total_input_tokens() -> int:
    """获取总输入token数量"""
    return _total_input_tokens


def get_total_output_tokens() -> int:
    """获取总输出token数量"""
    return _total_output_tokens


def get_total_tokens() -> int:
    """获取总token数量（输入+输出）"""
    return _total_input_tokens + _total_output_tokens


def reset_statistics() -> None:
    """重置所有统计数据"""
    global _total_llm_calls, _total_input_tokens, _total_output_tokens
    _total_llm_calls = 0
    _total_input_tokens = 0
    _total_output_tokens = 0


def _update_statistics(response) -> None:
    """更新统计数据，从LLM响应中提取token信息"""
    global _total_llm_calls, _total_input_tokens, _total_output_tokens
    _total_llm_calls += 1
    if hasattr(response, 'usage') and response.usage:
        _total_input_tokens += response.usage.prompt_tokens
        _total_output_tokens += response.usage.completion_tokens
