import asyncio
import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

from loguru import logger
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent, Tool
from openai import AsyncOpenAI

from mplfuzz.models import API, ArgumentExpr, Solution
from mplfuzz.utils.result import Err, Ok, Result


class MCPAPIResolver:
    def __init__(self):
        self.openai_client: Optional[AsyncOpenAI] = None
        self.model_name: Optional[str] = None

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
        
        response = await mcp_session.list_tools()
        tools = [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        } for tool in response.tools]

        tool_name = "__".join(api.name.split("."))
        query = f'请探索出工具{tool_name}的正确使用方法，使用成功时你会得到`{{"success": True, "msg": "..."}}`，否则得到错误信息`{{"success":False, "msg":"..."}}`。如果得到错误信息，则根据错误信息中的`msg`进行重试直到成功。请尽可能多的探索各种类型的输入。'

        history = [{"role": "user", "content": query}]

        solutions: list[Solution] = []
        max_failure = 10
        while True:
            if len(json.dumps(history)) > 16000:
                if len(solutions) > 0:
                    break
                else:
                    if max_failure > 0:
                        history = history[:1]
                        max_failure -= 1
                        continue
                    else:
                        return Err(f"拼尽全力，无法战胜{api.name}")
            try:
                response = await self.openai_client.chat.completions.create(
                    model=self.model_name, messages=history, tools=tools
                )
            except Exception as e:
                return Err(f"Error occurred while creating chat completion: {e}")
            message = response.choices[0].message

            # If this is neccessary? 
            if message.content:
                history.append({"role": "assistant", "content": message.content})

            # terminate condition
            if len(message.tool_calls) == 0:
                if len(solutions) == 0:
                    if max_failure > 0:
                        history = history[:1]
                        max_failure -= 1
                        continue
                    else:
                        return Err(f"拼尽全力，无法战胜{api.name}")
                else:
                    break

            # call each tool
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args: dict[str, str] = json.loads(tool_call.function.arguments)
                try:
                    result: CallToolResult = await asyncio.wait_for(
                        mcp_session.call_tool(tool_name, tool_args),
                        10.0
                    )
                except asyncio.TimeoutError:
                    return Err(f"Tool {tool_name} timed out after 10 seconds")
                result_content: list[TextContent] = result.content
                result_text: str = result_content[0].text
                if result.isError:
                    # return Err(f"Error occurred while calling tool {tool_name}: {result_text}")
                    result_text = json.dumps({"success": False, "msg":f"{result_text}"})

                try:
                    result_dict: dict = json.loads(result_text)
                except json.JSONDecodeError:
                    return Err(f"Invalid JSON response from tool {tool_name}:\n{result_text}")

                if result_dict["success"]:
                    api_exprs = [ArgumentExpr(name=k, expr=v) for k, v in tool_args.items()]
                    solutions.append(
                        Solution(
                            api_name=api.name, api_exprs=api_exprs, expect_result=result_dict["msg"]
                        )
                    )

                history.append(
                    {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": tool_call.function.arguments,
                                },
                            }
                        ],
                    }
                )
                history.append({"role": "tool", "tool_call_id": tool_call.id, "content": result_text})

        return Ok(solutions)
    