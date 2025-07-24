"""
時間解析模塊
支持兩種時間查詢方式：
1. 相對時間（回推N天/小時）
2. 絕對時間區間
"""

import re
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple


class TimeParser:
    """處理各種時間表達式的解析器"""
    
    def __init__(self):
        # 相對時間關鍵詞映射
        self.relative_patterns = {
            # 小時
            r'過去\s*(\d+)\s*小時': 'hours',
            r'最近\s*(\d+)\s*小時': 'hours', 
            r'(\d+)\s*小時內': 'hours',
            r'(\d+)\s*h': 'hours',
            r'(\d+)\s*hour[s]?': 'hours',
            
            # 天
            r'過去\s*(\d+)\s*天': 'days',
            r'最近\s*(\d+)\s*天': 'days',
            r'(\d+)\s*天內': 'days',
            r'(\d+)\s*d': 'days',
            r'(\d+)\s*day[s]?': 'days',
            
            # 週
            r'過去\s*(\d+)\s*週': 'weeks',
            r'最近\s*(\d+)\s*週': 'weeks',
            r'(\d+)\s*週內': 'weeks',
            r'(\d+)\s*w': 'weeks',
            r'(\d+)\s*week[s]?': 'weeks',
            
            # 月
            r'過去\s*(\d+)\s*月': 'months',
            r'最近\s*(\d+)\s*月': 'months',
            r'(\d+)\s*月內': 'months',
            r'(\d+)\s*M': 'months',
            r'(\d+)\s*month[s]?': 'months',
            
            # 特殊時間
            r'今天|today': 'today',
            r'昨天|yesterday': 'yesterday',
            r'上週|last\s*week': 'last_week',
            r'上月|last\s*month': 'last_month',
        }
    
    def parse_relative_time(self, text: str) -> Optional[Dict[str, str]]:
        """
        解析相對時間表達式
        返回OpenSearch相對時間格式
        """
        text = text.lower().strip()
        
        # 檢查特殊時間關鍵詞
        if any(pattern in text for pattern in ['今天', 'today']):
            return {'gte': 'now/d', 'lte': 'now', 'description': '今天'}
        
        if any(pattern in text for pattern in ['昨天', 'yesterday']):
            return {'gte': 'now-1d/d', 'lte': 'now-1d/d+1d', 'description': '昨天'}
        
        if any(pattern in text for pattern in ['上週', 'last week']):
            return {'gte': 'now-7d', 'lte': 'now', 'description': '過去一週'}
        
        if any(pattern in text for pattern in ['上月', 'last month']):
            return {'gte': 'now-30d', 'lte': 'now', 'description': '過去一個月'}
        
        # 解析數字相對時間
        for pattern, unit in self.relative_patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if unit in ['today', 'yesterday', 'last_week', 'last_month']:
                    continue  # 已經處理過
                
                try:
                    number = int(match.group(1))
                    return self._convert_to_opensearch_relative(number, unit)
                except (ValueError, IndexError):
                    continue
        
        return None
    
    def parse_absolute_time(self, start_time: str, end_time: str) -> Optional[Dict[str, str]]:
        """
        解析絕對時間區間
        支持多種日期格式
        """
        try:
            # 解析開始時間
            start_dt = self._parse_datetime(start_time)
            end_dt = self._parse_datetime(end_time)
            
            if start_dt and end_dt:
                # 轉換為ISO格式給OpenSearch
                start_iso = start_dt.isoformat()
                end_iso = end_dt.isoformat()
                
                return {
                    'gte': start_iso,
                    'lte': end_iso,
                    'description': f'從 {start_dt.strftime("%Y-%m-%d %H:%M")} 到 {end_dt.strftime("%Y-%m-%d %H:%M")}'
                }
        except Exception as e:
            print(f"時間解析錯誤: {e}")
            return None
        
        return None
    
    def _parse_datetime(self, time_str: str) -> Optional[datetime]:
        """解析各種格式的日期時間字符串"""
        time_str = time_str.strip()
        
        # 常見格式列表
        formats = [
            '%Y-%m-%d %H:%M:%S',
            '%Y-%m-%d %H:%M',
            '%Y-%m-%d',
            '%Y/%m/%d %H:%M:%S',
            '%Y/%m/%d %H:%M',
            '%Y/%m/%d',
            '%m-%d %H:%M',
            '%m/%d %H:%M',
            '%m-%d',
            '%m/%d',
        ]
        
        # 跳過dateutil，直接使用標準格式解析
        
        # 嘗試標準格式
        for fmt in formats:
            try:
                dt = datetime.strptime(time_str, fmt)
                # 如果沒有年份，使用當前年份
                if dt.year == 1900:
                    dt = dt.replace(year=datetime.now().year)
                return dt
            except ValueError:
                continue
        
        return None
    
    def _convert_to_opensearch_relative(self, number: int, unit: str) -> Dict[str, str]:
        """轉換為OpenSearch相對時間格式"""
        unit_map = {
            'hours': 'h',
            'days': 'd', 
            'weeks': 'w',
            'months': 'M'
        }
        
        opensearch_unit = unit_map.get(unit, 'd')
        
        # 處理週
        if unit == 'weeks':
            number = number * 7
            opensearch_unit = 'd'
        
        # 處理月（近似為30天）
        if unit == 'months':
            number = number * 30
            opensearch_unit = 'd'
        
        description_map = {
            'hours': f'過去{number}小時',
            'days': f'過去{number}天',
            'weeks': f'過去{number//7}週',
            'months': f'過去{number//30}個月'
        }
        
        return {
            'gte': f'now-{number}{opensearch_unit}',
            'lte': 'now',
            'description': description_map.get(unit, f'過去{number}{opensearch_unit}')
        }
    
    def analyze_time_query(self, query: str) -> Dict[str, any]:
        """
        分析查詢中的時間表達式
        返回時間解析結果和建議
        """
        result = {
            'has_time': False,
            'time_range': None,
            'suggestions': [],
            'type': None  # 'relative' or 'absolute'
        }
        
        # 檢查相對時間
        relative_result = self.parse_relative_time(query)
        if relative_result:
            result['has_time'] = True
            result['time_range'] = relative_result
            result['type'] = 'relative'
            return result
        
        # 檢查是否包含絕對時間線索
        absolute_indicators = [
            r'\d{4}-\d{2}-\d{2}',  # YYYY-MM-DD
            r'\d{4}/\d{2}/\d{2}',  # YYYY/MM/DD
            r'\d{2}-\d{2}',        # MM-DD
            r'\d{2}/\d{2}',        # MM/DD
        ]
        
        for pattern in absolute_indicators:
            if re.search(pattern, query):
                result['suggestions'].append("檢測到日期格式，請提供完整的時間區間（開始時間和結束時間）")
                result['type'] = 'absolute'
                break
        
        # 如果沒有檢測到時間，提供建議
        if not result['has_time'] and not result['suggestions']:
            result['suggestions'] = [
                "可以使用相對時間：'過去24小時'、'過去7天'、'昨天'等",
                "或指定絕對時間區間：'2025-07-01 到 2025-07-10'"
            ]
        
        return result


def create_time_aware_prompt(original_query: str, time_parser: TimeParser) -> str:
    """
    創建包含時間處理指導的查詢prompt
    """
    time_analysis = time_parser.analyze_time_query(original_query)
    
    base_prompt = f"用戶查詢: {original_query}\n\n"
    
    if time_analysis['has_time']:
        time_range = time_analysis['time_range']
        base_prompt += f"檢測到時間範圍: {time_range['description']}\n"
        base_prompt += f"OpenSearch時間格式: {{'gte': '{time_range['gte']}', 'lte': '{time_range['lte']}'}}\n\n"
        base_prompt += "請使用上述時間範圍構建OpenSearch查詢。\n"
    else:
        if time_analysis['suggestions']:
            base_prompt += "時間處理建議:\n"
            for suggestion in time_analysis['suggestions']:
                base_prompt += f"- {suggestion}\n"
            base_prompt += "\n"
    
    return base_prompt