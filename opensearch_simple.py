#!/usr/bin/env python3
"""
簡化版OpenSearch Agent，專注於基本工具調用
"""
import asyncio
import time

from mcp_agent.app import MCPApp
from mcp_agent.config import (
    GoogleSettings,
    Settings,
    LoggerSettings,
    MCPSettings,
    MCPServerSettings,
)
from mcp_agent.agents.agent import Agent
from mcp_agent.workflows.llm.augmented_llm_google import GoogleAugmentedLLM

settings = Settings(
    execution_engine="asyncio",
    logger=LoggerSettings(type="console", level="info"),  # 減少debug輸出
    mcp=MCPSettings(
        servers={
            "opensearch": MCPServerSettings(
                transport="streamable_http",
                url="http://localhost:9900/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json"
                },
                description="OpenSearch MCP Server"
            ),
        }
    ),
    google=GoogleSettings(
        default_model="gemini-2.0-flash",
    ),
)

# 使用配置檔案而不是程式化設定，這樣才能讀取secrets
app = MCPApp(name="opensearch_simple")  # 移除 settings=settings

async def simple_test():
    """簡單測試，專注於基本工具調用"""
    async with app.run() as agent_app:
        logger = agent_app.logger
        
        opensearch_agent = Agent(
            name="opensearch_agent",
            instruction="""You are an OpenSearch assistant. Use the available tools to help users query OpenSearch indices. 
            When asked to list indices, use ListIndexTool. Be concise and helpful.""",
            server_names=["opensearch"],
        )

        async with opensearch_agent:
            print("\n=== OpenSearch 簡化版代理 ===")
            
            # 列出可用工具
            tools_result = await opensearch_agent.list_tools()
            print(f"✅ 連接成功，發現 {len(tools_result.tools)} 個工具")
            
            llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
            
            # 測試基本查詢
            test_queries = [
                "List the first 5 indices using ListIndexTool",
                "Show me all EDR related indices",
                "Get information about the edr-agents-000001 index"
            ]
            
            for i, query in enumerate(test_queries, 1):
                print(f"\n🔍 測試查詢 {i}: {query}")
                try:
                    result = await llm.generate_str(message=query)
                    print(f"✅ 結果: {result}")
                except Exception as e:
                    print(f"❌ 錯誤: {e}")
                
                # 短暫暫停
                await asyncio.sleep(1)

async def interactive_mode():
    """互動模式"""
    async with app.run() as agent_app:
        opensearch_agent = Agent(
            name="opensearch_agent",
            instruction="""You are an OpenSearch assistant. Use available tools to help users with OpenSearch queries.
            Available tools include ListIndexTool, SearchIndexTool, etc. Be helpful and concise.""",
            server_names=["opensearch"],
        )

        async with opensearch_agent:
            print("\n=== OpenSearch 互動代理 ===")
            print("輸入 'quit' 退出")
            
            llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
            
            while True:
                try:
                    user_query = input("\n🔍 請輸入查詢: ").strip()
                    
                    if user_query.lower() in ['quit', 'exit', '退出']:
                        print("再見！")
                        break
                    
                    if not user_query:
                        continue
                    
                    print(f"⏳ 處理中...")
                    result = await llm.generate_str(message=user_query)
                    print(f"\n📊 結果:\n{result}")
                    
                except KeyboardInterrupt:
                    print("\n\n退出中...")
                    break
                except Exception as e:
                    print(f"❌ 錯誤: {e}")

if __name__ == "__main__":
    import sys
    
    start = time.time()
    
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print("🧪 執行自動測試...")
        asyncio.run(simple_test())
    else:
        print("🚀 啟動互動模式...")
        asyncio.run(interactive_mode())
    
    end = time.time()
    print(f"\n總執行時間: {end - start:.2f}秒")