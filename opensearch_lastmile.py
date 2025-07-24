import asyncio
import time
import re
from typing import List, Any, Optional

from pydantic import BaseModel, Field

from mcp_agent.app import MCPApp
from time_parser import TimeParser, create_time_aware_prompt
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
    """OpenSearch搜尋結果的結構化表示"""
    query: str = Field(default="未知查詢", description="原始查詢語句")
    total_hits: int = Field(default=0, description="找到的記錄總數")
    results: List[str] = Field(default=[], description="搜尋結果摘要清單")
    summary: str = Field(default="無法生成摘要", description="簡短的中文摘要說明")


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
            5. Ask for clarification if the search query is ambiguous (如果搜尋查詢不明確請要求澄清)
            
            OpenSearch DSL Query Examples (OpenSearch DSL 查詢範例):
            
            1. Basic term query (基本詞彙查詢):
            {
              "query": {
                "term": {
                  "event_type": "agent_stop"
                }
              }
            }
            
            2. Multi-index search with specific event (多索引特定事件搜尋):
            {
              "query": {
                "bool": {
                  "must": [
                    {"term": {"event_type": "agent_stop"}}
                  ]
                }
              },
              "sort": [{"timestamp": {"order": "desc"}}],
              "size": 10
            }
            
            3. Range query with time filter (時間範圍查詢):
            {
              "query": {
                "bool": {
                  "must": [
                    {"term": {"event_type": "agent_stop"}},
                    {"range": {"timestamp": {"gte": "now-24h", "lte": "now"}}}
                  ]
                }
              }
            }
            
            4. Match query for text search (文字搜尋查詢):
            {
              "query": {
                "match": {
                  "message": "error occurred"
                }
              }
            }
            
            Always use proper DSL syntax like the examples above when constructing queries.
            總是使用上述範例中的正確 DSL 語法來構建查詢。
            
            Time Range Guidelines (時間範圍指南):
            - For "past 24 hours" or "last day": use "now-24h" to "now"
            - For "past week": use "now-7d" to "now"  
            - For "past month": use "now-30d" to "now"
            - For "today": use "now/d" to "now"
            - For "yesterday": use "now-1d/d" to "now-1d/d+1d"
            
            當用戶要求查詢特定時間範圍時，直接使用 OpenSearch 的相對時間語法，
            不需要詢問當前時間。使用 "now" 相對時間表達式。""",
            server_names=["opensearch"],
        )

        async with opensearch_agent:
            logger.info("opensearch_searcher: Connected to server, calling list_tools...")
            result = await opensearch_agent.list_tools()
            logger.info("Tools available:", data=result.model_dump())

            llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
            time_parser = TimeParser()

            # Interactive search loop
            print("\n=== OpenSearch Agent 已啟動 ===")
            print("請輸入您的搜尋查詢，輸入 'quit' 退出")
            print("💡 時間查詢提示:")
            print("   • 相對時間: '過去24小時', '過去7天', '昨天', '上週'")
            print("   • 絕對時間: 輸入開始和結束時間，如 '2025-07-01 到 2025-07-10'")
            
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
                    
                    # 檢查是否為絕對時間區間查詢
                    if '到' in user_query or ' to ' in user_query.lower():
                        # 處理絕對時間區間
                        parts = re.split(r'到|to', user_query, flags=re.IGNORECASE)
                        if len(parts) == 2:
                            start_time = parts[0].strip()
                            end_time = parts[1].strip()
                            
                            # 嘗試解析絕對時間
                            time_range = time_parser.parse_absolute_time(start_time, end_time)
                            if time_range:
                                print(f"⏰ 檢測到時間區間: {time_range['description']}")
                                enhanced_query = f"""執行 OpenSearch 查詢，包含時間範圍限制：
                                原始查詢: {user_query}
                                時間範圍: {{'range': {{'@timestamp': {{'gte': '{time_range['gte']}', 'lte': '{time_range['lte']}'}}}}}}
                                
                                請構建包含此時間範圍的 OpenSearch DSL 查詢。"""
                            else:
                                print("⚠️ 時間格式無法解析，將使用原始查詢")
                                enhanced_query = f"Execute search query in OpenSearch: {user_query}"
                        else:
                            enhanced_query = f"Execute search query in OpenSearch: {user_query}"
                    else:
                        # 使用時間解析器分析查詢
                        time_aware_prompt = create_time_aware_prompt(user_query, time_parser)
                        enhanced_query = f"Execute search query in OpenSearch: {time_aware_prompt}"
                    
                    # Execute search query
                    result = await llm.generate_str(message=enhanced_query)
                    logger.info(f"Search result for '{user_query}': {result}")
                    print(f"\n📊 搜尋結果:\n{result}")
                    
                    # Generate structured summary only if we got results
                    if result and len(result.strip()) > 0:
                        try:
                            # Debug: 檢查傳入LLM的參數
                            structured_message = f"""分析以下OpenSearch搜尋結果並提取關鍵信息：

                            查詢: {user_query}
                            搜尋結果: {result}

                            請從搜尋結果中提取：
                            1. 總記錄數量（查找數字如10000、>10000等）
                            2. 主要搜尋結果摘要
                            3. 簡短中文說明

                            如果看到"超過10000筆"、"10000+"等描述，total_hits請設為實際數字而非0。
                            不須調用MCP工具，只需生成結構化摘要。"""
                            
                            print(f"\n🔍 Debug - 傳入LLM的message長度: {len(structured_message)}")
                            print(f"🔍 Debug - response_model類型: {SearchResult}")
                            print(f"🔍 Debug - 原始搜尋結果長度: {len(result)}")
                            
                            structured_result = await llm.generate_structured(
                                message=structured_message,
                                response_model=SearchResult,
                            )
                            
                            # Debug: 檢查返回的結果
                            print(f"\n🔍 Debug - structured_result類型: {type(structured_result)}")
                            print(f"🔍 Debug - structured_result是否為None: {structured_result is None}")
                            
                            # 檢查是否為ValidationError
                            if hasattr(structured_result, 'errors'):
                                print(f"❌ 檢測到ValidationError: {structured_result}")
                                print(f"🔍 Debug - ValidationError詳細信息: {structured_result.errors()}")
                                raise structured_result
                            elif structured_result and isinstance(structured_result, SearchResult):
                                print(f"🔍 Debug - structured_result內容: {structured_result}")
                                print(f"🔍 Debug - query屬性: {hasattr(structured_result, 'query')}")
                                print(f"🔍 Debug - total_hits屬性: {hasattr(structured_result, 'total_hits')}")
                                
                                print(f"\n📋 結構化摘要:")
                                print(f"   查詢: {getattr(structured_result, 'query', '未知查詢')}")  
                                print(f"   總命中數: {getattr(structured_result, 'total_hits', 0)}")
                                print(f"   摘要: {getattr(structured_result, 'summary', '無法生成摘要')}")
                            else:
                                print(f"⚠️ structured_result類型不正確或為None: {type(structured_result)}")
                                print(f"🔍 Debug - 內容: {structured_result}")
                                
                            logger.info(f"Structured search result: {structured_result}")
                        except Exception as e:
                            error_msg = str(e) if hasattr(e, '__str__') else type(e).__name__
                            print(f"⚠️ 結構化摘要生成失敗: {error_msg}")
                            
                            # 特別處理ValidationError
                            if hasattr(e, 'errors'):
                                print(f"🔍 Debug - ValidationError詳細信息: {e.errors()}")
                            
                            logger.error(f"Structured summary generation failed: {error_msg}", exc_info=True)
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