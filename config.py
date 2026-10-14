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

# 中概互联网成分股（35只：22港股+13美股ADR，去重，主要上市地优先）
# 港股PE用akshare财务数据实时计算；美股ADR用参考PE（已校准）
CHINA_INTL_STOCKS = {
    # --- 港股（22只，主要上市地）---
    "00700.HK": "腾讯控股",
    "09988.HK": "阿里巴巴-W",
    "01810.HK": "小米集团-W",
    "09999.HK": "网易-SW",
    "09618.HK": "京东集团-SW",
    "09961.HK": "携程集团-S",
    "09888.HK": "百度集团-SW",
    "01024.HK": "快手-W",
    "06618.HK": "京东健康",
    "01698.HK": "腾讯音乐-SW",
    "09626.HK": "哔哩哔哩-W",
    "03888.HK": "金山软件",
    "00136.HK": "中国儒意",
    "00241.HK": "阿里健康",
    "00780.HK": "同程旅行",
    "06060.HK": "众安在线",
    "01357.HK": "美图公司",
    "02400.HK": "心动公司",
    "01833.HK": "平安好医生",
    "09997.HK": "京东物流",
    "00941.HK": "中国移动",
    "02518.HK": "汽车之家-S",
    # --- 美股ADR（13只，无对应港股主要上市的或纯美股中概）---
    "BABA": "阿里巴巴-W(ADR)",
    "PDD": "拼多多(ADR)",
    "JD": "京东集团-SW(ADR)",
    "NTES": "网易-SW(ADR)",
    "WB": "微博(ADR)",
    "FUTU": "富途控股(ADR)",
    "YUMC": "百胜中国(ADR)",
    "GDS": "世纪互联(ADR)",
    "LI": "理想汽车(ADR)",
    "MOMO": "陌陌(ADR)",
    "TIGR": "老虎证券(ADR)",
    "VIPS": "唯品会(ADR)",
    "SOHU": "搜狐(ADR)",
}

# 美股ADR参考PE（已校准，来源：Yahoo Finance / 市场公认）
# 仅用于PE计算，历史序列用同一值
US_REF_PE = {
    "BABA": 17.45, "PDD": 33.61, "JD": 8.0, "NTES": 15.0,
    "WB": 25.04, "FUTU": 80.49, "YUMC": 17.11, "GDS": 36.01,
    "LI": 15.06, "MOMO": 14.09, "TIGR": 67.81, "VIPS": 5.0,
    "SOHU": 9.0,
}
