"""数据源抽象层 - ABC 定义"""
from abc import ABC, abstractmethod
import pandas as pd


class DataSource(ABC):

    @abstractmethod
    def get_constituents(self, index_code: str) -> pd.DataFrame:
        """获取指数当前成分股列表"""
        pass

    @abstractmethod
    def get_daily_quotes(self, stock_code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """获取单只股票历史日K线 + 流通股数"""
        pass

    @abstractmethod
    def get_financial_reports(self, stock_code: str) -> pd.DataFrame:
        """获取单只股票利润表（季报/年报）"""
        pass

    @abstractmethod
    def get_index_pe_history(self, index_name: str) -> pd.DataFrame:
        """获取指数月度PE历史（乐咕乐股数据，用于验证）"""
        pass


class DataSourceFactory:
    """数据源工厂"""
    _sources = {}

    @classmethod
    def register(cls, name: str, source_class):
        cls._sources[name] = source_class

    @classmethod
    def create(cls, name: str) -> DataSource:
        if name not in cls._sources:
            raise ValueError(f"Unknown data source: {name}")
        return cls._sources[name]()
