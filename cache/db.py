"""SQLite 缓存层"""
import sqlite3
import json
import time
from pathlib import Path
from typing import Optional
import pandas as pd
from config import DB_PATH


class CacheDB:
    """本地 SQLite 缓存，避免重复下载"""

    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _init_tables(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS stock_quotes (
                    code TEXT NOT NULL,
                    date TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL,
                    volume REAL, amount REAL,
                    outstanding_share REAL, turnover REAL,
                    updated_at REAL,
                    PRIMARY KEY (code, date)
                );

                CREATE TABLE IF NOT EXISTS stock_financials (
                    code TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    net_profit_attr REAL,
                    publish_date TEXT NOT NULL,
                    period_type TEXT,
                    updated_at REAL,
                    PRIMARY KEY (code, report_date)
                );

                CREATE TABLE IF NOT EXISTS stock_quarterly_profit (
                    code TEXT NOT NULL,
                    report_date TEXT NOT NULL,
                    quarter_profit REAL NOT NULL,
                    publish_date TEXT NOT NULL,
                    PRIMARY KEY (code, report_date)
                );

                CREATE TABLE IF NOT EXISTS index_constituents (
                    index_code TEXT NOT NULL,
                    stock_code TEXT NOT NULL,
                    stock_name TEXT,
                    fetched_at REAL,
                    PRIMARY KEY (index_code, stock_code)
                );

                CREATE TABLE IF NOT EXISTS index_pe_daily (
                    trade_date TEXT NOT NULL,
                    index_code TEXT NOT NULL,
                    total_market_cap REAL,
                    total_profit_ttm REAL,
                    pe_ratio REAL,
                    stock_count INTEGER,
                    updated_at REAL,
                    PRIMARY KEY (trade_date, index_code)
                );

                CREATE TABLE IF NOT EXISTS index_pe_stats (
                    trade_date TEXT NOT NULL,
                    index_code TEXT NOT NULL,
                    pe_mean REAL,
                    pe_std REAL,
                    pe_high_line REAL,
                    pe_low_line REAL,
                    pe_percentile REAL,
                    updated_at REAL,
                    PRIMARY KEY (trade_date, index_code)
                );

                CREATE TABLE IF NOT EXISTS fetch_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    table_name TEXT NOT NULL,
                    key TEXT NOT NULL,
                    status TEXT NOT NULL,
                    record_count INTEGER,
                    error_msg TEXT,
                    created_at REAL
                );
            """)

    def save_quotes(self, code: str, df: pd.DataFrame):
        """保存日K线数据"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM stock_quotes WHERE code=?", (code,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    code, row["date"], row.get("open"), row.get("high"),
                    row.get("low"), row.get("close"), row.get("volume"),
                    row.get("amount"), row.get("outstanding_share"),
                    row.get("turnover"), time.time()
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO stock_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                records
            )

    def load_quotes(self, code: str, start_date: str = "", end_date: str = "") -> pd.DataFrame:
        """加载日K线数据"""
        with self._conn() as conn:
            sql = "SELECT date, open, high, low, close, volume, amount, outstanding_share, turnover FROM stock_quotes WHERE code=?"
            params = [code]
            if start_date:
                sql += " AND date >= ?"
                params.append(start_date)
            if end_date:
                sql += " AND date <= ?"
                params.append(end_date)
            sql += " ORDER BY date"
            return pd.read_sql_query(sql, conn, params=params)

    def upsert_quotes(self, code: str, df: pd.DataFrame):
        """增量合并日K线（只插入新日期，不删除已有）"""
        if df.empty:
            return
        with self._conn() as conn:
            records = []
            for _, row in df.iterrows():
                records.append((
                    code, row["date"], row.get("open"), row.get("high"),
                    row.get("low"), row.get("close"), row.get("volume"),
                    row.get("amount"), row.get("outstanding_share"),
                    row.get("turnover"), time.time()
                ))
            conn.executemany(
                "INSERT OR IGNORE INTO stock_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                records
            )

    def save_financials(self, code: str, df: pd.DataFrame):
        """保存财务数据（含期间类型）"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM stock_financials WHERE code=?", (code,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    code,
                    row["report_date"].strftime("%Y-%m-%d"),
                    row["net_profit_attr"],
                    row["publish_date"].strftime("%Y-%m-%d"),
                    row.get("period_type", ""),
                    time.time()
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO stock_financials VALUES (?,?,?,?,?,?)",
                records
            )

    def save_quarterly_profits(self, code: str, df: pd.DataFrame):
        """保存拆分后的单季利润"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM stock_quarterly_profit WHERE code=?", (code,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    code,
                    row["report_date"].strftime("%Y-%m-%d"),
                    row["quarter_profit"],
                    row["publish_date"].strftime("%Y-%m-%d"),
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO stock_quarterly_profit VALUES (?,?,?,?)",
                records
            )

    def load_quarterly_profits(self, code: str) -> pd.DataFrame:
        """加载拆分后的单季利润"""
        with self._conn() as conn:
            df = pd.read_sql_query(
                "SELECT report_date, quarter_profit, publish_date FROM stock_quarterly_profit WHERE code=? ORDER BY report_date",
                conn, params=[code]
            )
            if not df.empty:
                df["report_date"] = pd.to_datetime(df["report_date"])
                df["publish_date"] = pd.to_datetime(df["publish_date"])
            return df

    def load_financials(self, code: str) -> pd.DataFrame:
        """加载财务数据"""
        with self._conn() as conn:
            df = pd.read_sql_query(
                "SELECT report_date, net_profit_attr, publish_date FROM stock_financials WHERE code=? ORDER BY report_date",
                conn, params=[code]
            )
            if not df.empty:
                df["report_date"] = pd.to_datetime(df["report_date"])
                df["publish_date"] = pd.to_datetime(df["publish_date"])
            return df

    def save_constituents(self, index_code: str, df: pd.DataFrame):
        """保存成分股列表"""
        with self._conn() as conn:
            conn.execute("DELETE FROM index_constituents WHERE index_code=?", (index_code,))
            records = []
            for _, row in df.iterrows():
                records.append((index_code, row["code"], row.get("name", ""), time.time()))
            conn.executemany(
                "INSERT OR REPLACE INTO index_constituents VALUES (?,?,?,?)",
                records
            )

    def load_constituents(self, index_code: str) -> pd.DataFrame:
        """加载成分股列表"""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT stock_code as code, stock_name as name FROM index_constituents WHERE index_code=?",
                conn, params=[index_code]
            )

    def save_pe_daily(self, index_code: str, df: pd.DataFrame):
        """保存每日PE数据"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM index_pe_daily WHERE index_code=?", (index_code,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    row["trade_date"], index_code,
                    row.get("total_market_cap"), row.get("total_profit_ttm"),
                    row.get("pe_ratio"), row.get("stock_count"),
                    time.time()
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO index_pe_daily VALUES (?,?,?,?,?,?,?)",
                records
            )

    def load_pe_daily(self, index_code: str) -> pd.DataFrame:
        """加载每日PE数据"""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM index_pe_daily WHERE index_code=? ORDER BY trade_date",
                conn, params=[index_code]
            )

    def save_pe_stats(self, index_code: str, df: pd.DataFrame):
        """保存统计指标"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM index_pe_stats WHERE index_code=?", (index_code,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    row["trade_date"], index_code,
                    row.get("pe_mean"), row.get("pe_std"),
                    row.get("pe_high_line"), row.get("pe_low_line"),
                    row.get("pe_percentile"), time.time()
                ))
            conn.executemany(
                "INSERT OR REPLACE INTO index_pe_stats VALUES (?,?,?,?,?,?,?,?)",
                records
            )

    def load_pe_stats(self, index_code: str) -> pd.DataFrame:
        """加载统计指标"""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM index_pe_stats WHERE index_code=? ORDER BY trade_date",
                conn, params=[index_code]
            )

    def get_last_updated(self, index_code: str) -> Optional[str]:
        """获取指数最近更新日期"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM index_pe_daily WHERE index_code=?",
                (index_code,)
            ).fetchone()
            return row[0] if row and row[0] else None

    def clear_index(self, index_code: str):
        """清除指数的所有缓存数据"""
        with self._conn() as conn:
            conn.execute("DELETE FROM stock_quotes")  # keep all quotes (shared)
            conn.execute("DELETE FROM index_pe_daily WHERE index_code=?", (index_code,))
            conn.execute("DELETE FROM index_pe_stats WHERE index_code=?", (index_code,))
            conn.execute("DELETE FROM index_constituents WHERE index_code=?", (index_code,))
