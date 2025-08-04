import asyncio
import time
import re
from typing import List, Any, Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
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


# ===== 原有的資料模型和工具函數 =====
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


# ===== API 請求/回應模型 =====
class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

class ChatResponse(BaseModel):
    content: List[dict]  # Following assistant-ui format

class SearchRequest(BaseModel):
    query: str

class SearchResponse(BaseModel):
    query: str
    result: str
    structured_report: Optional[SecurityEventReport] = None


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


# ===== MCP 設定 =====
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
mcp_app = MCPApp(name="opensearch_agent")

# ===== 全域變數 =====
opensearch_agent: Optional[Agent] = None
time_parser: Optional[TimeParser] = None
agent_app = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """應用程式生命週期管理"""
    global opensearch_agent, time_parser, agent_app
    
    print("🚀 正在初始化 OpenSearch Agent API...")
    
    try:
        # 初始化 MCP 應用
        agent_app = await mcp_app.run().__aenter__()
        logger = agent_app.logger
        
        print("✅ MCP 應用初始化完成")
        
        # 初始化 OpenSearch Agent
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
        
        # 初始化時間解析器
        time_parser = TimeParser()
        
        # 測試 Agent 連接
        await opensearch_agent.__aenter__()
        tools_result = await opensearch_agent.list_tools()
        print(f"✅ 成功連接 OpenSearch，發現 {len(tools_result.tools)} 個工具")
        
        # 初始化 LLM 連接
        llm = await opensearch_agent.attach_llm(GoogleAugmentedLLM)
        print("✅ LLM 連接就緒")
        
        print("🎉 OpenSearch Agent API 初始化完成！")
        
        yield
        
    except Exception as e:
        print(f"❌ 初始化失敗: {e}")
        raise
    finally:
        print("🔄 正在關閉 OpenSearch Agent API...")
        try:
            if opensearch_agent:
                await opensearch_agent.__aexit__(None, None, None)
            if agent_app:
                await agent_app.__aexit__(None, None, None)
        except Exception as e:
            print(f"⚠️ 關閉時發生錯誤: {e}")


# ===== FastAPI 應用初始化 =====
app = FastAPI(
    title="OpenSearch Security Analysis API",
    description="OpenSearch 資安事件分析 API 服務",
    version="1.0.0",
    lifespan=lifespan
)

# 設定 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],  # Next.js 預設端口
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ===== 核心搜尋處理函數 =====
async def process_search_query(user_query: str) -> tuple[str, Optional[SecurityEventReport]]:
    """處理搜尋查詢並返回結果和結構化報告"""
    global opensearch_agent, time_parser
    
    if not opensearch_agent or not time_parser:
        raise HTTPException(status_code=503, detail="OpenSearch Agent 未初始化")
    
    try:
        print(f"⏳ 正在執行搜尋: {user_query}")
        
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
        
        # 執行搜尋查詢
        result = await fresh_llm.generate_str(message=enhanced_query)
        print(f"📊 搜尋結果長度: {len(result)}")
        
        # 生成結構化摘要
        structured_report = None
        if result and len(result.strip()) > 0:
            try:
                # 先檢查搜尋結果是否包含實際數據
                has_actual_data = any(indicator in result.lower() for indicator in [
                    'hits', 'total', '_source', 'timestamp', '_id', 'found', 'documents', 'records', 'count',
                    'docs:', 'size:', 'indices', '索引', '以下是', '結果顯示', '查詢結果'
                ])
                
                has_error_indicators = any(error in result.lower() for error in [
                    'connection refused', 'timeout', 'network error', 'server error', 'parse error',
                    '連接被拒絕', '網路錯誤', '伺服器錯誤', '解析錯誤'
                ])
                
                if has_error_indicators or not has_actual_data:
                    # 如果搜尋失敗或沒有實際數據，創建錯誤報告
                    structured_report = SecurityEventReport(
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
                    structured_report = await fresh_llm.generate_structured(
                        message=structured_message,
                        response_model=SecurityEventReport,
                    )
                    
                print(f"✅ 結構化報告生成完成")
                
            except Exception as e:
                print(f"⚠️ 結構化摘要生成失敗: {e}")
                # 創建基本的錯誤報告
                structured_report = SecurityEventReport(
                    query=user_query,
                    total_hits=0,
                    description=f"結構化分析失敗: {str(e)}"
                )
        
        return result, structured_report
        
    except Exception as e:
        print(f"❌ 搜尋執行失敗: {e}")
        raise HTTPException(status_code=500, detail=f"搜尋執行失敗: {str(e)}")


def format_search_result(result: str, structured_report: Optional[SecurityEventReport]) -> str:
    """格式化搜尋結果為顯示格式"""
    if not structured_report:
        return result
    
    # 基本資訊
    total_hits = getattr(structured_report, 'total_hits', 0)
    severity = getattr(structured_report, 'severity', '中')
    description = getattr(structured_report, 'description', '無描述')
    
    # 獲取安全狀態指示器
    status_indicator = get_security_status_indicator(severity, total_hits)
    
    # 檢查是否為查詢失敗的情況
    if (total_hits == 0 and 
        any(keyword in description for keyword in ['查詢失敗', '查詢執行失敗', '無資料', '無實際數據', 'unknown key', 'parse', 'error'])):
        formatted_result = f"""📊 搜尋結果:
{result}

[ ❌ 查詢執行失敗 ]
📄 摘要：OpenSearch 查詢處理錯誤
📋 錯誤詳情：{description}

💡 可能原因：
• OpenSearch DSL 查詢語法錯誤
• 索引映射配置問題
• 查詢欄位名稱不匹配
• OpenSearch 版本相容性問題"""
    else:
        # 正常的安全報告格式
        formatted_result = f"""📊 搜尋結果:
{result}

[ {status_indicator} ]
📄 摘要：{description}
🕒 時間：{getattr(structured_report, 'event_time', '未知時間')}
👤 使用者：{getattr(structured_report, 'username', '未知使用者')}
💻 主機：{getattr(structured_report, 'hostname', '未知主機')}
🌐 IP：{getattr(structured_report, 'host_ip', '未知IP')}"""
        
        # 建議行動
        actions = getattr(structured_report, 'recommended_actions', [])
        if actions and actions != ['查詢未執行'] and actions != ['無資料']:
            combined_actions = "，".join(actions)
            formatted_result += f"\n✅ 建議：{combined_actions}"
        
        # 完整日誌展開功能
        log_samples = getattr(structured_report, 'log_samples', [])
        if log_samples and log_samples != ['查詢未執行'] and log_samples != ['無資料']:
            formatted_result += f"\n\n[ 🔍 展開完整日誌 ▼ ]"
            max_logs = min(3, len(log_samples))
            for i, log in enumerate(log_samples[:max_logs]):
                formatted_result += f"\n\n--- 日誌 {i+1}/{max_logs} ---\n{format_log_sample(log)}"
            
            if len(log_samples) > 3:
                formatted_result += f"\n\n... 還有 {len(log_samples) - 3} 筆日誌 (已省略)"
    
    formatted_result += f"\n\n📊 總計：{total_hits} 筆記錄"
    return formatted_result


# ===== API 端點 =====
@app.get("/")
async def root():
    """根端點 - 健康檢查"""
    return {
        "message": "OpenSearch Security Analysis API", 
        "version": "1.0.0",
        "status": "running",
        "timestamp": time.time()
    }

@app.get("/health")
async def health():
    """健康檢查端點"""
    global opensearch_agent
    
    status = "healthy" if opensearch_agent else "not_ready"
    return {
        "status": status,
        "timestamp": time.time(),
        "agent_ready": opensearch_agent is not None
    }

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """聊天端點 - 相容 assistant-ui 格式"""
    try:
        # 取得最後一個使用者訊息
        user_messages = [msg for msg in request.messages if msg.role == "user"]
        if not user_messages:
            raise HTTPException(status_code=400, detail="找不到使用者訊息")
        
        last_user_message = user_messages[-1].content
        print(f"🔍 處理聊天查詢: {last_user_message}")
        
        # 處理查詢
        result, structured_report = await process_search_query(last_user_message)
        
        # 格式化結果
        formatted_result = format_search_result(result, structured_report)
        
        # 回傳 assistant-ui 相容格式
        return ChatResponse(
            content=[{"type": "text", "text": formatted_result}]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ 聊天端點錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"處理聊天訊息失敗: {str(e)}")

@app.post("/search", response_model=SearchResponse)
async def search_endpoint(request: SearchRequest):
    """直接搜尋端點"""
    try:
        print(f"🔍 處理搜尋查詢: {request.query}")
        
        # 處理查詢
        result, structured_report = await process_search_query(request.query)
        
        # 格式化結果
        formatted_result = format_search_result(result, structured_report)
        
        return SearchResponse(
            query=request.query,
            result=formatted_result,
            structured_report=structured_report
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ 搜尋端點錯誤: {e}")
        raise HTTPException(status_code=500, detail=f"搜尋失敗: {str(e)}")

@app.get("/tools")
async def list_tools():
    """列出可用的 OpenSearch 工具"""
    global opensearch_agent
    
    if not opensearch_agent:
        raise HTTPException(status_code=503, detail="OpenSearch Agent 未初始化")
    
    try:
        tools_result = await opensearch_agent.list_tools()
        return {
            "tools_count": len(tools_result.tools),
            "tools": [
                {
                    "name": tool.name,
                    "description": tool.description
                }
                for tool in tools_result.tools
            ]
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"無法列出工具: {str(e)}")


# ===== 測試連接功能 =====
async def test_connection():
    """測試 OpenSearch MCP 連接"""
    global opensearch_agent
    
    if not opensearch_agent:
        return {"status": "error", "message": "Agent 未初始化"}
    
    try:
        tools_result = await opensearch_agent.list_tools()
        return {
            "status": "success",
            "message": f"成功連接，找到 {len(tools_result.tools)} 個工具",
            "tools": [tool.name for tool in tools_result.tools]
        }
    except Exception as e:
        return {"status": "error", "message": f"連接失敗: {str(e)}"}

@app.get("/test")
async def test_endpoint():
    """測試連接端點"""
    return await test_connection()


if __name__ == "__main__":
    import uvicorn
    print("🚀 啟動 OpenSearch Agent API Server...")
    uvicorn.run(
        "opensearch_agent_api:app",
        host="0.0.0.0", 
        port=8000,
        reload=False,  # 避免重載時的初始化問題
        log_level="info"
    )