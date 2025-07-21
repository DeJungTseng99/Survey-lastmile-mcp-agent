#!/usr/bin/env python3
"""
使用MCP streamablehttp_client直接測試連接
"""
import asyncio
from mcp.client.streamable_http import streamablehttp_client
from mcp.client.session import ClientSession

async def test_mcp_connection():
    """使用MCP客戶端直接測試連接"""
    print("🚀 開始直接MCP連接測試...")
    
    try:
        async with streamablehttp_client("http://localhost:9900/mcp") as (read_stream, write_stream, _):
            async with ClientSession(read_stream, write_stream) as session:
                print("✅ Connected to MCP Server")
                
                # 初始化MCP會話
                print("🔧 正在初始化MCP會話...")
                result = await session.initialize()
                print(f"✅ 初始化成功！服務器資訊: {result}")
                
                # 列出可用工具
                print("🔍 列出可用工具...")
                tools_result = await session.list_tools()
                print(f"📋 發現 {len(tools_result.tools)} 個工具:")
                
                for i, tool in enumerate(tools_result.tools, 1):
                    print(f"   {i}. {tool.name}")
                    if hasattr(tool, 'description') and tool.description:
                        print(f"      描述: {tool.description}")
                    if hasattr(tool, 'inputSchema') and tool.inputSchema:
                        print(f"      輸入參數: {list(tool.inputSchema.get('properties', {}).keys())}")
                
                # 列出可用提示
                try:
                    print("\n🔍 列出可用提示...")
                    prompts_result = await session.list_prompts()
                    print(f"📝 發現 {len(prompts_result.prompts)} 個提示:")
                    
                    for i, prompt in enumerate(prompts_result.prompts, 1):
                        print(f"   {i}. {prompt.name}")
                        if hasattr(prompt, 'description') and prompt.description:
                            print(f"      描述: {prompt.description}")
                except Exception as e:
                    print(f"⚠️ 無法列出提示: {e}")
                
                # 嘗試調用ListIndexTool（如果存在）
                for tool in tools_result.tools:
                    if 'index' in tool.name.lower() or 'list' in tool.name.lower():
                        print(f"\n🔧 嘗試調用工具: {tool.name}")
                        try:
                            tool_result = await session.call_tool(tool.name, {})
                            print(f"✅ 工具調用成功: {tool_result}")
                        except Exception as e:
                            print(f"❌ 工具調用失敗: {e}")
                        break
                
                return True
                
    except Exception as e:
        print(f"❌ 連接失敗: {e}")
        return False

async def main():
    success = await test_mcp_connection()
    if success:
        print("\n🎉 MCP連接測試成功！")
        print("你現在可以在 opensearch_lastmile.py 中使用這個配置")
    else:
        print("\n💥 MCP連接測試失敗")
        print("請檢查OpenSearch MCP Server是否正確運行")

if __name__ == "__main__":
    asyncio.run(main())