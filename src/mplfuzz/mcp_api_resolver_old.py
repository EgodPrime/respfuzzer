import json
from datetime import timedelta
from typing import Optional

from loguru import logger
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from openai import AsyncOpenAI

from mplfuzz.models import API, ArgumentExpr, Solution
from mplfuzz.utils.config import get_config
from mplfuzz.utils.result import Err, Ok, Result


class MCPAPIResolver:
    def __init__(self):
        self.openai_client: Optional[AsyncOpenAI] = None
        self.model_name: Optional[str] = None
        self.config = get_config("mcp_api_resolver").value

    async def setup_llm(self, model_config: dict[str, str]) -> Result[None, str]:
        try:
            self.openai_client = AsyncOpenAI(
                base_url=model_config.get("base_url"),
                api_key=model_config.get("api_key"),
            )
        except Exception as e:
            return Err(f"Failed to create AsyncOpenAI client: {e}")

        self.model_name = model_config.get("model_name")
        try:
            models = await self.openai_client.models.list()
        except Exception as e:
            return Err(f"Failed to list models: {e}")
        model_names = [model.id for model in models.data]
        if self.model_name not in model_names:
            return Err(f"Model {self.model_name} not found. Available models: {model_names}")

        return Ok()

    async def solve_api(self, api: API, mcp_session: ClientSession) -> Result[list[Solution], str]:
        if not self.openai_client:
            return Err("OpenAI client not initialized. Call setup_llm first.")

        completion = await mcp_session.list_tools()
        tools = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.inputSchema,
                },
            }
            for tool in completion.tools
        ]

        tool_name = "__".join(api.api_name.split("."))
        query = f'/no_think请探索出工具{tool_name}的正确使用方法，使用成功时你会得到`{{"success": True, "msg": "..."}}`，否则得到错误信息`{{"success":False, "msg":"..."}}`。如果得到错误信息，则根据错误信息中的`msg`进行重试直到成功。请尽可能多的探索各种类型的输入。'
        # logger.debug(f"query llm with {query}")

        history = []
        history.append({"role": "user", "content": query})

        max_failure = self.config.get("max_failure", 10)
        history_length_limit = self.config.get("history_length_limit", 14000)
        output_length_limit = self.config.get("output_length_limit", 2000)

        solutions: list[Solution] = []
        while max_failure > 0:

            if len(json.dumps(history)) > history_length_limit:

                if len(solutions) > 0:  # break if any solutions are found

                    break
                else:

                    if max_failure > 0:  # else if no solutions are found, reset history and retry

                        history = history[:1]
                        max_failure -= 1
                        continue
                    else:  # if max_failure is 0, return error

                        return Err(f"拼尽全力，无法战胜{api.api_name}")

            # Ask llm to create tool call
            # logger.debug(f"Query llm for {api.name}")
            try:
                completion = await self.openai_client.chat.completions.create(
                    messages=history,
                    model=self.model_name,
                    tools=tools,
                    max_completion_tokens=output_length_limit,
                )
            except Exception as e:
                return Err(f"Error occurred while creating completion: {e}")
            # logger.debug(f"llm generate tool call: {completion.choices[0].message.tool_calls}")

            # terminate condition
            if completion.choices[0].message.tool_calls is None or len(completion.choices[0].message.tool_calls) == 0:
                if len(solutions) == 0:
                    if max_failure > 0:
                        history = history[:1]
                        max_failure -= 1
                        continue
                    else:
                        return Err(f"拼尽全力，无法战胜{api.api_name}")
                else:
                    break

            history.append(completion.choices[0].message.to_dict())

            # call each tool
            for tool_call in completion.choices[0].message.tool_calls:

                tool_name = tool_call.function.name
                tool_args: dict[str, str] = json.loads(tool_call.function.arguments)
                try:
                    logger.debug(f"Try call {api.api_name} with {tool_args}")
                    result: CallToolResult = await mcp_session.call_tool(tool_name, tool_args, timedelta(seconds=10))

                    result_content: list[TextContent] = result.content
                    result_text: str = result_content[0].text
                    if result.isError:
                        result_text = json.dumps({"success": False, "msg": f"{result_text}"})
                except Exception as e:
                    result_text = json.dumps({"success": False, "msg": f"{e}"})
                # logger.debug(f"Tool {tool_name} returned: {result_text}")

                try:
                    result_dict: dict = json.loads(result_text)
                except json.JSONDecodeError:
                    return Err(f"Invalid JSON response from tool {tool_name}:\n{result_text}")

                if result_dict["success"]:
                    api_exprs = [ArgumentExpr(name=k, expr=str(v)) for k, v in tool_args.items()]
                    solutions.append(
                        Solution(api_name=api.api_name, api_exprs=api_exprs, expect_result=result_dict["msg"])
                    )
                    logger.info(f"Found a solution for {api.api_name}")

                history.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})

        return Ok(solutions)
