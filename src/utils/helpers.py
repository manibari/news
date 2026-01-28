"""
輔助函數模組
"""

import hashlib
import re
from datetime import datetime
from typing import Optional

from dateutil import parser as date_parser


def parse_date(date_str: Optional[str]) -> Optional[datetime]:
    """
    解析各種格式的日期字串

    Args:
        date_str: 日期字串

    Returns:
        datetime 物件，解析失敗則回傳 None
    """
    if not date_str:
        return None

    try:
        return date_parser.parse(date_str)
    except (ValueError, TypeError):
        return None


def clean_text(text: Optional[str]) -> Optional[str]:
    """
    清理文字內容：移除多餘空白、HTML 標籤等

    Args:
        text: 原始文字

    Returns:
        清理後的文字
    """
    if not text:
        return None

    # 移除 HTML 標籤
    text = re.sub(r'<[^>]+>', '', text)

    # 移除多餘空白
    text = re.sub(r'\s+', ' ', text)

    # 移除首尾空白
    text = text.strip()

    return text if text else None


def generate_hash(text: str) -> str:
    """
    產生文字的 MD5 雜湊值（用於去重）

    Args:
        text: 輸入文字

    Returns:
        MD5 雜湊字串
    """
    return hashlib.md5(text.encode('utf-8')).hexdigest()
