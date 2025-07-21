#!/usr/bin/env python3
"""
測試不同MCP端點的連線工具
"""
import asyncio
import aiohttp
import json

async def test_http_endpoint(url, path=""):
    """測試HTTP端點是否回應"""
    test_url = f"{url}{path}"
    print(f"\n🔍 測試端點: {test_url}")
    
    try:
        async with aiohttp.ClientSession() as session:
            # 嘗試MCP初始化請求
            mcp_request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0"}
                }
            }
            
            async with session.post(
                test_url, 
                json=mcp_request,
                headers={"Content-Type": "application/json"}
            ) as response:
                print(f"   狀態碼: {response.status}")
                print(f"   Content-Type: {response.headers.get('content-type', 'unknown')}")
                
                if response.status == 200:
                    try:
                        result = await response.json()
                        print(f"   ✅ 成功！回應: {json.dumps(result, indent=2)}")
                        return True
                    except:
                        text = await response.text()
                        print(f"   ⚠️ 非JSON回應: {text[:200]}...")
                elif response.status == 404:
                    print(f"   ❌ 404 Not Found - 端點不存在")
                else:
                    text = await response.text()
                    print(f"   ⚠️ 其他錯誤: {text[:200]}...")
                    
    except Exception as e:
        print(f"   ❌ 連線錯誤: {e}")
    
    return False

async def main():
    base_url = "http://localhost:9900"
    
    print("🚀 開始測試OpenSearch MCP Server端點...")
    print(f"基礎URL: {base_url}")
    
    # 測試常見的MCP端點
    endpoints_to_test = [
        "",           # 根路徑
        "/mcp",       # MCP路徑
        "/api/mcp",   # API MCP路徑
        "/v1/mcp",    # 版本化MCP路徑
        "/rpc",       # RPC路徑
        "/jsonrpc",   # JSON-RPC路徑
    ]
    
    successful_endpoints = []
    
    for endpoint in endpoints_to_test:
        if await test_http_endpoint(base_url, endpoint):
            successful_endpoints.append(f"{base_url}{endpoint}")
    
    print(f"\n📊 測試完成!")
    if successful_endpoints:
        print(f"✅ 成功的端點:")
        for endpoint in successful_endpoints:
            print(f"   - {endpoint}")
    else:
        print("❌ 沒有找到有效的MCP端點")
        print("\n🔧 建議檢查:")
        print("   1. OpenSearch MCP Server是否在port 9900運行？")
        print("   2. Server的正確端點路徑是什麼？")
        print("   3. Server是否需要特殊的認證或headers？")

if __name__ == "__main__":
    asyncio.run(main())