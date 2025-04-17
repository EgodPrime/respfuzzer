import json
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, TextContent, Tool
from openai import AsyncOpenAI

from mplfuzz.models import API, ArgumentExpr, Solution
from mplfuzz.utils.result import Err, Ok, Result


class MCPAPIResolver:
    def __init__(self):
        self.mcp_session: Optional[ClientSession] = None
        self.openai_client: Optional[AsyncOpenAI] = None
        self.model_name: Optional[str] = None
        self.exit_stack = AsyncExitStack()
        self.tools: list[Tool] = []

    async def connect_to_mcp_server(self, file_path: Path|str) -> Result[None, str]:
        if self.mcp_session:
            await self.exit_stack.aclose()

        if isinstance(file_path, Path):
            file_path = str(file_path)
        
        try:
            server_params = StdioServerParameters(command="python", args=[file_path], env=None)
            stdio_transport = await self.exit_stack.enter_async_context(stdio_client(server_params, errlog=None))
            self.stdio, self.write = stdio_transport
            self.mcp_session = await self.exit_stack.enter_async_context(
                ClientSession(self.stdio, self.write)
            )

            await self.mcp_session.initialize()
        except Exception as e:
            return Err(f"Failed to connect to MCP server {file_path}: {e}")

        # 列出可用工具
        response = await self.mcp_session.list_tools()
        self.tools = [{
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.inputSchema
            }
        } for tool in response.tools]
        return Ok()

    async def setup_llm(self, model_config: dict[str, str]) -> Result[None, str]:
        try:
            self.openai_client = AsyncOpenAI(
                base_url=model_config.get("base_url"),
                api_key=model_config.get("api_key"),
            )
        except Exception as e:
            return Err(f"Failed to create AsyncOpenAI client: {e}")

        self.model_name = model_config.get("model_name")
        models = await self.openai_client.models.list()
        model_names = [model.id for model in models.data]
        if self.model_name not in model_names:
            return Err(f"Model {self.model_name} not found. Available models: {model_names}")

        return Ok()

    async def solve_api(self, api: API) -> Result[list[Solution], str]:
        if not self.tools:
            return Err("Session not initialized. Call setup_llm first.")
        if not self.openai_client:
            return Err("OpenAI client not initialized. Call setup_llm first.")

        tool_name = "__".join(api.name.split("."))
        query = f'我想让你探索出工具{tool_name}的正确使用方法，使用成功时你会得到`{{"success": True, "msg": "..."}}`，否则得到错误信息`{{"success":False, "msg":"..."}}`。如果得到错误信息，则根据错误信息中的`msg`进行重试直到成功。请尽可能多的探索各种类型的输入'

        history = [{"role": "user", "content": query}]

        solutions: list[Solution] = []
        while True:
            response = await self.openai_client.chat.completions.create(
                model=self.model_name, messages=history, tools=self.tools
            )
            message = response.choices[0].message

            if message.content:
                history.append({"role": "assistant", "content": message.content})

            # terminate condition
            if len(message.tool_calls) == 0:
                break

            # call each tool
            for tool_call in message.tool_calls:
                tool_name = tool_call.function.name
                tool_args: dict[str, str] = json.loads(tool_call.function.arguments)
                result: CallToolResult = await self.mcp_session.call_tool(tool_name, tool_args)
                result_content: list[TextContent] = result.content
                result_text: str = result_content[0].text
                if result.isError:
                    return Err(f"Error from tool {tool_name}: {result_text}")

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

    async def clean_up(self):
        await self.exit_stack.aclose()
