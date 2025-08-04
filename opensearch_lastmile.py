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


class SecurityEventReport(BaseModel):
    """資安事件分析報告"""
    query: str = Field(default="未知查詢", description="原始查詢語句")
    total_hits: int = Field(default=0, description="找到的記錄總數")
    event_time: str = Field(default="未知時間", description="事件發生時間 (YYYY-MM-DD HH:mm:ss)")
    event_type: str = Field(default="未知事件", description="事件類型，如：登入失敗、檔案刪除、異常流量")
    severity: str = Field(default="中", description="嚴重性：低/中/高")
    username: str = Field(default="未知使用者", description="發生事件的帳號")
    hostname: str = Field(default="未知主機", description="發生事件的設備名稱")
    host_ip: str = Field(default="未知IP", description="發生事件的 IP")
    description: str = Field(default="無描述", description="事件詳細描述")
    recommended_actions: List[str] = Field(default=[], description="建議採取的行動")
    log_samples: List[str] = Field(default=[], description="2-3條具代表性的日誌內容")


def get_security_status_indicator(severity: str, total_hits: int) -> str:
    """根據嚴重程度和記錄數量返回安全狀態指示器"""
    severity_lower = severity.lower()
    
    if severity_lower == "高" or total_hits > 100:
        return "🔴 高風險警示"
    elif severity_lower == "中" or total_hits > 10:
        return "🟡 中度警示"
    elif severity_lower == "低" or total_hits > 0:
        return "🟠 低度警示"
    else:
        return "✅ 安全狀態"


def extract_hit_count_from_text(text: str) -> int:
    """從文字中提取記錄數量"""
    import re
    
    # 尋找各種可能的數字表達方式
    patterns = [
        r'(\d+)\s*筆',  # "23筆"
        r'(\d+)\s*條',  # "23條"
        r'(\d+)\s*個',  # "23個"
        r'(\d+)\s*筆符合',  # "23筆符合"
        r'共有\s*(\d+)',  # "共有23"
        r'找到了?\s*(\d+)',  # "找到23" 或 "找到了23"
        r'(\d+)\s*(?:筆|條|個).*?符合',  # "23筆符合條件"
        r'(?:結果|記錄|日誌).*?(\d+)',  # "結果顯示23"
    ]
    
    for pattern in patterns:
        matches = re.findall(pattern, text)
        if matches:
            try:
                return int(matches[0])
            except ValueError:
                continue
    
    return 0

def format_log_sample(log_sample: str, max_lines: int = 10) -> str:
    """格式化日誌樣本，限制顯示行數"""
    try:
        import json
        # 嘗試格式化 JSON
        parsed = json.loads(log_sample)
        formatted = json.dumps(parsed, indent=2, ensure_ascii=False)
        lines = formatted.split('\n')
        if len(lines) > max_lines:
            return '\n'.join(lines[:max_lines]) + '\n  ...(已截斷)'
        return formatted
    except:
        # 如果不是 JSON，直接返回
        return log_sample


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
 ## prompt限制LLM只能是查詢助手。LLM一開始就不是資安專家?
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
            
            OpenSearch DSL Query Guidelines (OpenSearch DSL 查詢指南):
            
            1. Use term queries for exact matches (精確匹配使用 term 查詢)
            2. Use match queries for text search (文字搜尋使用 match 查詢) 
            3. Use bool queries to combine conditions (使用 bool 查詢組合條件)
            4. Use range queries for time/numeric filters (時間/數值範圍使用 range 查詢)
            5. Support multi-index searches with flexible patterns (支援彈性模式的多索引搜尋)
            6. Automatically determine appropriate field names and values (自動判斷適當的欄位名稱和值)
            
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
        # initialize agent，理論上會先完成tool/list與連接LLM的功能。但先執行了print跟input
        async with opensearch_agent:
            print(f"[{time.time()}] 開始初始化")
            logger.info("opensearch_searcher: Connected to server, calling list_tools...")
            print(f"[{time.time()}] 呼叫 list_tools 前")
            result = await opensearch_agent.list_tools()
            print(f"[{time.time()}] list_tools 完成")
            print(f"Tools available: {len(result.tools)} 個工具")
            for i, tool in enumerate(result.tools, 1):
                print(f"  {i}. {tool.name}: {tool.description}")
            print(f"[{time.time()}] 初始化完成")

            llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
            time_parser = TimeParser()
              # ===== 新增：強制初始化確認 =====
            logger.info("Agent initialization completed, tools and LLM ready")
            print("\\n🔧 正在初始化Agent和LLM連接...")

            # 測試連接是否正常
            try:
                test_tools = await opensearch_agent.list_tools()
                print(f"✅ 成功載入 {len(test_tools.tools)} 個工具")
                print("✅ LLM 連接就緒")
            except Exception as e:
                print(f"❌ 初始化失敗: {e}")
                return
            # ===== 新增結束 =====

            # Interactive search loop
            print("\n=== OpenSearch 資安事件分析系統 已啟動 ===")
            print("🔍 請輸入您的搜尋查詢，輸入 'quit' 退出")
            print("📋 系統會自動生成詳細的資安事件分析報告")
            print("\n💡 查詢建議:")
            print("   • 事件查詢: 'authentication 過去24小時', '登入失敗 過去7天'")
            print("   • 時間範圍: '過去24小時', '昨天', '2025-07-01 到 2025-07-10'")
            print("   • 特定事件: 'event.category:authentication', 'failed login'")
            print("   • 使用者查詢: 'username:eagle_tseng 過去1週'")
            print("-" * 55)
            
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
                    
                    # 每次查詢都使用新的LLM實例，徹底避免記憶干擾
                    fresh_llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
                    
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
                                
                                重要：請實際使用opensearch_search_logs_advanced工具來執行此DSL查詢，不要只回應查詢語法。"""
                            else:
                                print("⚠️ 時間格式無法解析，將使用原始查詢")
                                enhanced_query = f"""Execute search query in OpenSearch using available MCP tools: {user_query}

                                    重要：請實際使用以下其中一個OpenSearch MCP工具來執行搜尋：
                                    1. opensearch_search_logs_by_keyword - 用於關鍵字搜尋  
                                    2. opensearch_search_logs_advanced - 用於複雜的DSL查詢
                                    3. opensearch_list_log_indices - 列出可用的索引

                                    不要只回應查詢語法，請實際調用工具並返回搜尋結果。"""
                        else:
                            enhanced_query = f"Execute search query in OpenSearch: {user_query}"
                    else:
                        # 使用時間解析器分析查詢
                        time_aware_prompt = create_time_aware_prompt(user_query, time_parser)
                        enhanced_query = f"""Execute search query in OpenSearch using available MCP tools: {time_aware_prompt}

                            **執行搜尋查詢：**
                            原始用戶查詢（請嚴格按照此查詢執行，不要修改任何條件）: "{user_query}"
                            
                            【重要】請完全按照用戶的原始查詢執行，不要擅自修改任何查詢條件或關鍵字。
                            例如：如果用戶說"status為Inactive"，請確保查詢欄位為"status"，值為"Inactive"，不要改成其他形式。
                            
                            請使用 opensearch_search_logs_advanced 工具執行搜尋：
                            - 自動判斷合適的索引模式（可搜尋多個索引）
                            - 根據使用者查詢內容構建適當的 DSL 查詢
                            - 支援搜尋任何欄位和值
                            - 請直接執行搜尋並返回實際結果，不要只提供查詢語法
                            - 嚴格保持原始查詢條件不變

                            請立即調用工具並返回實際的搜尋結果。"""
                    
                    # Execute search query
                    result = await fresh_llm.generate_str(message=enhanced_query)
                    logger.info(f"Search result for '{user_query}': {result}")
                    print(f"\n📊 搜尋結果:\n{result}")
                    
                    # 檢查是否為無效的搜尋結果（只有查詢語法而沒有實際數據）
                    # 更精確的檢測：只有當結果包含工具調用語法但沒有實際數據時才警告
                    has_tool_syntax = any(keyword in result.lower() for keyword in [
                        'tool_code', 'tool_name', 'tool_input', '```json', '好的，我將使用'
                    ])
                    
                    has_actual_data = any(indicator in result.lower() for indicator in [
                        'hits', 'total', '_source', 'timestamp', '_id', 'found', 'documents', 'records'
                    ])
                    
                    is_query_only = has_tool_syntax and not has_actual_data
                    
                    if is_query_only:
                        print("⚠️ 檢測到查詢語法但無實際搜尋結果，可能是OpenSearch服務器未連接")
                        print("💡 建議：請確認OpenSearch MCP服務器是否正在運行")
                    
                    # Generate structured summary only if we got results
                    if result and len(result.strip()) > 0:
                        try:
                            # 先檢查搜尋結果是否包含實際數據
                            has_actual_data = any(indicator in result.lower() for indicator in [
                                'hits', 'total', '_source', 'timestamp', '_id', 'found', 'documents', 'records', 'count'
                            ])
                            
                            has_error_indicators = any(error in result.lower() for error in [
                                'error', 'failed', 'unknown key', 'parse', 'invalid', '錯誤', '失敗'
                            ])
                            
                            if has_error_indicators or not has_actual_data:
                                # 如果搜尋失敗或沒有實際數據，創建錯誤報告
                                structured_result = SecurityEventReport(
                                    query=user_query,
                                    total_hits=0,
                                    event_time="N/A",
                                    event_type="查詢失敗",
                                    severity="無法評估",
                                    username="N/A",
                                    hostname="N/A", 
                                    host_ip="N/A",
                                    description=f"查詢執行失敗: {result[:200]}...",
                                    recommended_actions=["檢查 OpenSearch 服務器狀態", "驗證查詢語法", "確認網路連接"],
                                    log_samples=["無數據 - 查詢失敗"]
                                )
                            else:
                                # 只有在有實際數據時才進行LLM分析
                                structured_message = f"""分析以下OpenSearch搜尋結果並提取結構化資訊：

                                    原始查詢: {user_query}
                                    搜尋結果: {result}

                                    請分析上述結果並提取：
                                    1. total_hits: 實際找到的記錄數量
                                    2. event_time: 事件發生時間（從@timestamp提取）
                                    3. event_type: 事件類型（從event.type提取）
                                    4. severity: 嚴重程度（低/中/高，根據事件內容判斷）
                                    5. username: 使用者名稱（如有）
                                    6. hostname: 主機名稱（從host.name提取）
                                    7. host_ip: IP地址（如有）
                                    8. description: 事件摘要描述
                                    9. recommended_actions: 建議的處理行動
                                    10. log_samples: 代表性的日誌內容

                                    注意：
                                    - 如果搜尋失敗或無數據，total_hits設為0
                                    - 無法取得的欄位使用預設值（N/A或未知）
                                    - 只分析提供的資料，不要執行額外搜尋"""
                                                                    
                                # 使用同一個fresh_llm實例進行結構化分析
                                structured_result = await fresh_llm.generate_structured(
                                    message=structured_message,
                                    response_model=SecurityEventReport,
                                )
                                # 診斷用：檢查搜尋結果中的實際數量
                                detected_hits = extract_hit_count_from_text(result)
                                print(f"\n🔍 Debug - 從搜尋結果中提取到的記錄數: {detected_hits}")
                                print(f"🔍 Debug - structured_result.total_hits: {getattr(structured_result, 'total_hits', 'NO_ATTR')}")
                                print(f"🔍 Debug - 數量是否匹配: {detected_hits > 0 and getattr(structured_result, 'total_hits', 0) > 0}")
                                print(f"🔍 Debug - 傳入LLM的message長度: {len(structured_message)}")
                            
                            print(f"🔍 Debug - response_model類型: {SecurityEventReport}")
                            print(f"🔍 Debug - 原始搜尋結果長度: {len(result)}")
                            
                            # Debug: 檢查返回的結果
                            print(f"\n🔍 Debug - structured_result類型: {type(structured_result)}")
                            print(f"🔍 Debug - structured_result是否為None: {structured_result is None}")
                            
                            # 檢查是否為ValidationError
                            if hasattr(structured_result, 'errors'):
                                print(f"❌ 檢測到ValidationError: {structured_result}")
                                print(f"🔍 Debug - ValidationError詳細信息: {structured_result.errors()}")
                                raise structured_result
                            elif structured_result and isinstance(structured_result, SecurityEventReport):
                                print(f"🔍 Debug - structured_result內容: {structured_result}")
                                print(f"🔍 Debug - query屬性: {hasattr(structured_result, 'query')}")
                                print(f"🔍 Debug - total_hits屬性: {hasattr(structured_result, 'total_hits')}")
                                
                                # 顯示新格式的資安事件分析報告
                                description = getattr(structured_result, 'description', '無描述')
                                total_hits = getattr(structured_result, 'total_hits', 0)
                                severity = getattr(structured_result, 'severity', '中')
                                
                                # 獲取安全狀態指示器
                                status_indicator = get_security_status_indicator(severity, total_hits)
                                
                                # 檢查是否為查詢失敗的情況
                                if (total_hits == 0 and 
                                    any(keyword in description for keyword in ['查詢失敗', '查詢執行失敗', '無資料', '無實際數據', 'unknown key', 'parse', 'error'])):
                                    print(f"\n[ ❌ 查詢執行失敗 ]")
                                    print(f"📄 摘要：OpenSearch 查詢處理錯誤")
                                    print(f"📋 錯誤詳情：{description}")
                                    print(f"\n💡 可能原因：")
                                    print(f"• OpenSearch DSL 查詢語法錯誤")
                                    print(f"• 索引映射配置問題")
                                    print(f"• 查詢欄位名稱不匹配")
                                    print(f"• OpenSearch 版本相容性問題")
                                else:
                                    # 正常的安全報告格式
                                    print(f"\n[ {status_indicator} ]")
                                    print(f"📄 摘要：{description}")
                                    print(f"🕒 時間：{getattr(structured_result, 'event_time', '未知時間')}")
                                    print(f"👤 使用者：{getattr(structured_result, 'username', '未知使用者')}")
                                    print(f"💻 主機：{getattr(structured_result, 'hostname', '未知主機')}")
                                    print(f"🌐 IP：{getattr(structured_result, 'host_ip', '未知IP')}")
                                    
                                    # 建議行動
                                    actions = getattr(structured_result, 'recommended_actions', [])
                                    if actions and actions != ['查詢未執行'] and actions != ['無資料']:
                                        # 合併所有建議為一行
                                        combined_actions = "，".join(actions)
                                        print(f"✅ 建議：{combined_actions}")
                                    
                                    # 完整日誌展開功能
                                    log_samples = getattr(structured_result, 'log_samples', [])
                                    if log_samples and log_samples != ['查詢未執行'] and log_samples != ['無資料']:
                                        print(f"\n[ 🔍 展開完整日誌 ▼ ]")
                                        # 最多顯示3筆日誌
                                        max_logs = min(3, len(log_samples))
                                        for i, log in enumerate(log_samples[:max_logs]):
                                            print(f"\n--- 日誌 {i+1}/{max_logs} ---")
                                            formatted_log = format_log_sample(log)
                                            print(formatted_log)
                                        
                                        if len(log_samples) > 3:
                                            print(f"\n... 還有 {len(log_samples) - 3} 筆日誌 (已省略)")
                                
                                print(f"\n📊 總計：{total_hits} 筆記錄")
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