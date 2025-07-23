import asyncio
import time

from pydantic import BaseModel

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


class SearchResult(BaseModel):
    query: str
    total_hits: int
    results: list
    summary: str


settings = Settings(
    execution_engine="asyncio",
    logger=LoggerSettings(type="console", level="debug"),
    mcp=MCPSettings(
        servers={
            "opensearch": MCPServerSettings(
                transport="streamable_http",
                url="http://localhost:9900/mcp",
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json"
                },
                description="OpenSearch MCP Server for data querying"
            ),
        }
    ),
    google=GoogleSettings(
        default_model="gemini-2.0-flash",
    ),
)

# 使用配置檔案而不是程式化設定，這樣才能讀取secrets
app = MCPApp(name="opensearch_agent")


async def test_connection():
    """Test connection to OpenSearch MCP server and list available tools"""
    async with app.run() as agent_app:
        logger = agent_app.logger
        context = agent_app.context

        logger.info("Testing connection to OpenSearch MCP server...")
        print("\n=== 測試OpenSearch MCP連線 ===")

        opensearch_agent = Agent(
            name="opensearch_tester",
            instruction="Test agent for connection verification",
            server_names=["opensearch"],
        )

        try:
            async with opensearch_agent:
                logger.info("opensearch_tester: 嘗試連接到MCP server...")
                print("✅ 成功建立與OpenSearch MCP server的連線")
                
                # List available tools
                tools_result = await opensearch_agent.list_tools()
                logger.info("Tools discovered:", data=tools_result.model_dump())
                
                print(f"\n📋 發現 {len(tools_result.tools)} 個可用工具:")
                for i, tool in enumerate(tools_result.tools, 1):
                    print(f"   {i}. {tool.name}")
                    if tool.description:
                        print(f"      描述: {tool.description[:100]}...")
                
                # List available prompts
                try:
                    prompts_result = await opensearch_agent.list_prompts()
                    print(f"\n📝 發現 {len(prompts_result.prompts)} 個可用提示:")
                    for i, prompt in enumerate(prompts_result.prompts, 1):
                        print(f"   {i}. {prompt.name}")
                        if prompt.description:
                            print(f"      描述: {prompt.description}")
                except Exception as e:
                    print(f"⚠️ 無法列出提示: {e}")
                
                return tools_result
                
        except Exception as e:
            logger.error(f"連線失敗: {e}")
            print(f"❌ 連線失敗: {e}")
            return None


async def example_usage():
    async with app.run() as agent_app:
        logger = agent_app.logger
        context = agent_app.context

        logger.info("Current config:", data=context.config.model_dump())

        opensearch_agent = Agent(
            name="opensearch_searcher",
            instruction="""You are an OpenSearch query agent with access to search capabilities. Please respond in Traditional Chinese (繁體中文).
            你是一個OpenSearch查詢助手，具有搜尋功能。請用繁體中文回應。
            Your job is to (你的工作是):
            1. Understand user search requests and translate them into appropriate OpenSearch queries (理解使用者的搜尋請求並轉換為適當的OpenSearch查詢)
            2. Execute the search using available tools (使用可用工具執行搜尋)
            3. Generate proper JSON-RPC 2.0 tool calls to the OpenSearch server (生成正確的JSON-RPC 2.0工具呼叫)
            4. Format and summarize the search results for the user (為使用者格式化和總結搜尋結果)
            5. Ask for clarification if the search query is ambiguous (如果搜尋查詢不明確請要求澄清)""",
            server_names=["opensearch"],
        )

        async with opensearch_agent:
            logger.info("opensearch_searcher: Connected to server, calling list_tools...")
            result = await opensearch_agent.list_tools()
            logger.info("Tools available:", data=result.model_dump())

            llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)

            # Interactive search loop
            print("\n=== OpenSearch Agent 已啟動 ===")
            print("請輸入您的搜尋查詢，輸入 'quit' 退出")
            
            while True:
                try:
                    user_query = input("\n🔍 請輸入搜尋查詢: ").strip()
                    
                    if user_query.lower() in ['quit', 'exit', '退出']:
                        print("感謝使用 OpenSearch Agent!")
                        break
                    
                    if not user_query:
                        print("請輸入有效的搜尋查詢")
                        continue
                    
                    print(f"\n⏳ 正在執行搜尋: {user_query}")
                    
                    # Execute search query
                    result = await llm.generate_str(
                        message=f"Execute search query in OpenSearch: {user_query}",
                    )
                    logger.info(f"Search result for '{user_query}': {result}")
                    print(f"\n📊 搜尋結果:\n{result}")
                    
                    # Generate structured summary only if we got results
                    if result and len(result.strip()) > 0:
                        try:
                            structured_result = await llm.generate_structured(
                                message="Create a structured summary of the previous search results, including the query, total hits found, and a brief summary.",
                                response_model=SearchResult,
                            )
                            print(f"\n📋 結構化摘要:")
                            print(f"   查詢: {structured_result.query}")
                            print(f"   總命中數: {structured_result.total_hits}")
                            print(f"   摘要: {structured_result.summary}")
                            logger.info(f"Structured search result: {structured_result}")
                        except Exception as e:
                            print(f"⚠️ 結構化摘要生成失敗: {e}")
                    else:
                        print("⚠️ 沒有獲得搜尋結果，跳過結構化摘要")
                    
                except KeyboardInterrupt:
                    print("\n\n收到中斷信號，正在退出...")
                    break
                except Exception as e:
                    logger.error(f"執行搜尋時發生錯誤: {e}")
                    print(f"❌ 搜尋失敗: {e}")


async def demo_usage():
    """Demo mode with predefined queries"""
    async with app.run() as agent_app:
        logger = agent_app.logger
        
        opensearch_agent = Agent(
            name="opensearch_searcher", 
            instruction="""You are an OpenSearch query agent. Execute searches and return results in a clear format.""",
            server_names=["opensearch"],
        )

        async with opensearch_agent:
            llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
            
            # Demo queries
            demo_queries = [
                "search for documents containing 'machine learning'",
                "find all entries with status='active' and timestamp from last week",
                "lookup user profiles where role='admin'"
            ]
            
            for query in demo_queries:
                print(f"\n🔍 Demo查詢: {query}")
                result = await llm.generate_str(message=f"Execute OpenSearch query: {query}")
                logger.info(f"Demo result: {result}")
                print(f"📊 結果: {result}")


if __name__ == "__main__":
    import sys
    
    start = time.time()
    
    # Check for different modes
    if len(sys.argv) > 1:
        mode = sys.argv[1]
        if mode == "test":
            print("🔧 啟動連線測試模式...")
            asyncio.run(test_connection())
        elif mode == "demo":
            print("🚀 啟動 Demo 模式...")
            asyncio.run(demo_usage())
        else:
            print(f"未知模式: {mode}")
            print("可用模式: test, demo, 或不指定參數進入互動模式")
    else:
        print("🚀 啟動互動模式...")
        asyncio.run(example_usage())
    
    end = time.time()
    t = end - start
    print(f"\nTotal run time: {t:.2f}s")