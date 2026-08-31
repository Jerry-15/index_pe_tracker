"""数据源包"""
from data_sources.base import DataSource
from data_sources.sina import SinaDataSource
from data_sources.base import DataSourceFactory

# 注册 Sina 数据源
DataSourceFactory.register("sina", SinaDataSource)


def get_source(name: str = "sina") -> DataSource:
    return DataSourceFactory.create(name)
