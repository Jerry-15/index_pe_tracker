"""
板块指数估值追踪器 - 配置文件
"""
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
CACHE_DIR = DATA_DIR / "cache"
DB_PATH = CACHE_DIR / "index_pe.db"

# 预设板块配置
INDEX_CONFIG = {
    "沪深300": {
        "code": "000300",
        "name": "沪深300",
        "source": "sina",
    },
    "中证500": {
        "code": "000905",
        "name": "中证500",
        "source": "sina",
    },
    "深证成指": {
        "code": "399001",
        "name": "深证成指",
        "source": "sina",
    },
    "创业板指": {
        "code": "399006",
        "name": "创业板指",
        "source": "sina",
    },
}

# 中概互联网指数配置
CHINA_INTL_CONFIG = {
    "code": "CHINA_INTL",
    "name": "中概互联网",
    "description": "美股/港股上市的中国互联网公司指数",
}

# 默认参数
DEFAULT_LOOKBACK_DAYS = 2000  # 约8年交易日
ROLLING_WINDOW = 2000         # 滚动窗口用于均值/标准差计算
MIN_PUBLISHED_DAYS = 10       # 财报发布后至少N天才能用于TTM计算

# API 限制
API_RETRY_TIMES = 3
API_RETRY_DELAY = 1.0  # 秒
API_BATCH_DELAY = 0.2  # 每批之间的延迟

# 参考PE数据源映射（乐咕乐股支持的指数名；None 表示无参考数据）
INDEX_REFERENCE = {
    "沪深300": "沪深300",
    "中证500": "中证500",
    "深证成指": None,
    "创业板指": None,
}

# Streamlit 图表配置
CHART_COLORS = {
    "pe": "#1f77b4",
    "mean": "#ff7f0e",
    "high_line": "#d62728",
    "low_line": "#2ca02c",
    "percentile": "#9467bd",
    "reference": "#7f7f7f",
}

# 中概互联网成分股（股票代码 -> 名称）
CHINA_INTL_STOCKS = {
    "BABA": "阿里巴巴",
    "PDD": "拼多多",
    "JD": "京东",
    "BIDU": "百度",
    "NTES": "网易",
    "MOMO": "陌陌",
    "TCEHY": "腾讯",
    "NCTY": "世纪华通",
    "VIPS": "唯品会",
    "WB": "微博",
    "IQ": "爱奇艺",
    "FENG": "凤凰卫视",
    "TCOM": "携程",
    "LI": "理想汽车",
    "NIO": "蔚来",
    "XPEV": "小鹏汽车",
    "TIGR": "老虎证券",
    "FUTU": "富途控股",
    "SINA": "新浪",
    "SOHU": "搜狐",
    "YUMC": "百胜中国",
    "GDS": "世纪互联",
    "VNET": "网宿科技",
}
