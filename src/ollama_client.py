from fastmcp import Client as MCPClient
from typing import List, Dict
import ollama
import asyncio
import sys

OLLAMA_MODEL="qwen2.5:3b"
MCP_SERVER_URL = "http://127.0.0.1:8080/mcp"

async def load_mcp_tools() -> List[Dict]:
    """Connect to MCP server and get list of available tools"""
    try:
        async with MCPClient(MCP_SERVER_URL) as mcp:
            tools_list = await mcp.list_tools()
            ollama_tools = []

            for tool in tools_list:
                ollama_tools.append({
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.inputSchema,
                    },
                })
            return ollama_tools
    except Exception as e:
        print(f"Error connecting to MCP server: {e}")
        sys.exit(1)
def ollama_chat(model_name, tools, prompt) -> Dict:
    final = ollama.chat(model=model_name, messages=[{"role": "user", "content": prompt}], tools=tools)
    if final['message'].get('tool_calls'):
        first_call = final['message']['tool_calls'][0]
        return {"tool_called": first_call["function"]["name"], "arguments": first_call["function"]["arguments"]}
    else:
        return {"tool_called": None, "answer": final["message"]["content"]}

if __name__ == "__main__":
    PROMPT = "show me all Pods pls"
    response = ollama_chat(OLLAMA_MODEL, asyncio.run(load_mcp_tools()), PROMPT)
    print(response)