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

                CREATE TABLE IF NOT EXISTS us_pe_daily (
                    trade_date TEXT NOT NULL,
                    pe_ratio REAL,
                    total_market_cap REAL,
                    stock_count INTEGER,
                    updated_at REAL,
                    PRIMARY KEY (trade_date)
                );

                CREATE TABLE IF NOT EXISTS us_pe_stats (
                    trade_date TEXT NOT NULL,
                    pe_mean REAL,
                    pe_std REAL,
                    pe_high_line REAL,
                    pe_low_line REAL,
                    pe_percentile REAL,
                    updated_at REAL,
                    PRIMARY KEY (trade_date)
                );

                CREATE TABLE IF NOT EXISTS us_stock_pe (
                    snapshot_date TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT,
                    price REAL,
                    eps_ttm_cny REAL,
                    eps_ttm_usd REAL,
                    pe REAL,
                    updated_at REAL,
                    PRIMARY KEY (snapshot_date, code)
                );
            """)
            self._migrate_h30533_tables(conn)

    @staticmethod
    def _migrate_h30533_tables(conn: sqlite3.Connection):
        """Create H30533 storage and add columns to older installations."""
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS h30533_constituent_snapshot (
                snapshot_date TEXT NOT NULL,
                index_code TEXT NOT NULL,
                security_code TEXT NOT NULL,
                name TEXT,
                exchange TEXT,
                weight REAL,
                issuer_id TEXT,
                security_type TEXT,
                ads_ratio REAL,
                is_duplicate_issuer INTEGER,
                source_type TEXT,
                as_of TEXT,
                constituent_as_of TEXT,
                raw_metadata TEXT,
                validity_reason TEXT,
                PRIMARY KEY (snapshot_date, index_code, security_code)
            );
            CREATE TABLE IF NOT EXISTS h30533_daily_valuation (
                trade_date TEXT NOT NULL,
                index_code TEXT NOT NULL,
                pe_ratio REAL,
                index_pe REAL,
                earnings_yield REAL,
                total_market_cap REAL,
                total_profit_ttm REAL,
                stock_count INTEGER,
                valid_count INTEGER,
                weight_coverage REAL,
                negative_profit_count INTEGER,
                missing_count INTEGER,
                 invalid_count INTEGER,
                 pe_mean REAL, pe_std REAL, pe_high_line REAL, pe_low_line REAL,
                 pe_percentile REAL,
                 source_type TEXT,
                source_date TEXT,
                constituent_as_of TEXT,
                constituent_basis TEXT,
                validity_reason TEXT,
                updated_at REAL,
                PRIMARY KEY (trade_date, index_code)
            );
            CREATE TABLE IF NOT EXISTS h30533_security_valuation (
                snapshot_date TEXT NOT NULL,
                index_code TEXT NOT NULL,
                security_code TEXT NOT NULL,
                name TEXT,
                issuer_id TEXT,
                weight REAL,
                price REAL,
                currency TEXT,
                market_cap REAL,
                verified_shares REAL,
                profit_ttm REAL,
                earnings_yield REAL,
                index_pe REAL,
                pe REAL,
                financial_as_of TEXT,
                source_date TEXT,
                source_type TEXT,
                source TEXT,
                data_source TEXT,
                validity_reason TEXT,
                PRIMARY KEY (snapshot_date, index_code, security_code)
            );
        """)
        migrations = {
            "h30533_constituent_snapshot": {
                "snapshot_date": "TEXT", "index_code": "TEXT", "security_code": "TEXT",
                "name": "TEXT", "exchange": "TEXT", "weight": "REAL",
                "issuer_id": "TEXT", "security_type": "TEXT", "ads_ratio": "REAL",
                "is_duplicate_issuer": "INTEGER",
                "source_type": "TEXT", "as_of": "TEXT", "constituent_as_of": "TEXT",
                "raw_metadata": "TEXT",
                "validity_reason": "TEXT",
            },
            "h30533_daily_valuation": {
                "trade_date": "TEXT", "index_code": "TEXT",
                "pe_ratio": "REAL", "index_pe": "REAL", "earnings_yield": "REAL",
                "total_market_cap": "REAL",
                "total_profit_ttm": "REAL", "stock_count": "INTEGER",
                "valid_count": "INTEGER", "weight_coverage": "REAL",
                "negative_profit_count": "INTEGER",
                "missing_count": "INTEGER", "invalid_count": "INTEGER", "source_type": "TEXT",
                "source_date": "TEXT", "constituent_as_of": "TEXT",
                "constituent_basis": "TEXT",
                "validity_reason": "TEXT", "updated_at": "REAL",
                "pe_mean": "REAL", "pe_std": "REAL", "pe_high_line": "REAL",
                "pe_low_line": "REAL", "pe_percentile": "REAL",
            },
            "h30533_security_valuation": {
                "snapshot_date": "TEXT", "index_code": "TEXT", "security_code": "TEXT",
                "name": "TEXT", "issuer_id": "TEXT", "weight": "REAL",
                "price": "REAL", "currency": "TEXT", "market_cap": "REAL",
                "verified_shares": "REAL",
                "profit_ttm": "REAL", "earnings_yield": "REAL", "pe": "REAL",
                "index_pe": "REAL", "financial_as_of": "TEXT", "source_date": "TEXT",
                "source_type": "TEXT", "source": "TEXT", "data_source": "TEXT",
                "validity_reason": "TEXT",
            },
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, sql_type in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")

    @staticmethod
    def _h30533_value(row, column):
        value = row.get(column)
        if pd.isna(value) if not isinstance(value, (dict, list)) else False:
            return None
        if column == "raw_metadata" and value is not None and not isinstance(value, str):
            return json.dumps(value, ensure_ascii=True, sort_keys=True)
        return value

    @staticmethod
    def _h30533_aliases(frame, aliases):
        frame = frame.copy()
        for canonical, alias in aliases.items():
            if canonical not in frame.columns and alias in frame.columns:
                frame[canonical] = frame[alias]
        return frame

    @staticmethod
    def _h30533_insert(conn, table, columns, records):
        placeholders = ",".join("?" for _ in columns)
        names = ", ".join(columns)
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({placeholders})",
            records,
        )

    def save_h30533_daily(self, index_code: str, df: pd.DataFrame):
        """Atomically replace the H30533 daily valuation rows for an index."""
        if df.empty:
            return
        if "trade_date" not in df.columns:
            raise KeyError("trade_date")
        columns = [
            "trade_date", "index_code", "pe_ratio", "earnings_yield",
            "index_pe", "total_market_cap", "total_profit_ttm", "stock_count", "valid_count",
            "weight_coverage",
            "negative_profit_count", "missing_count", "invalid_count", "source_type", "source_date",
            "constituent_as_of", "constituent_basis", "validity_reason", "updated_at",
            "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile",
        ]
        if not set(df.columns).intersection(set(columns[2:-1])):
            raise ValueError("H30533 daily valuation has no recognized fields")
        records = [tuple(self._h30533_value(row, column) if column != "index_code"
                         else index_code for column in columns)
                   for _, row in df.iterrows()]
        with self._conn() as conn:
            conn.execute("DELETE FROM h30533_daily_valuation WHERE index_code=?", (index_code,))
            self._h30533_insert(conn, "h30533_daily_valuation", columns, records)

    def load_h30533_daily(self, index_code: str) -> pd.DataFrame:
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM h30533_daily_valuation WHERE index_code=? ORDER BY trade_date",
                conn, params=[index_code],
            )

    def save_h30533_snapshot(self, index_code: str, constituents: pd.DataFrame,
                             daily: pd.DataFrame, securities: pd.DataFrame):
        """Atomically replace all H30533 data after validating every input row."""
        if constituents.empty or daily.empty or securities.empty:
            raise ValueError("H30533 snapshot parts must not be empty")
        constituents = self._h30533_aliases(
            constituents, {"as_of": "constituent_as_of"})
        constituents = self._h30533_aliases(
            constituents, {"constituent_as_of": "as_of"})
        daily = self._h30533_aliases(
            daily, {"pe_ratio": "index_pe", "stock_count": "valid_count"})
        daily = self._h30533_aliases(
            daily, {"index_pe": "pe_ratio", "valid_count": "stock_count"})
        securities = self._h30533_aliases(
            securities, {"pe": "index_pe", "data_source": "source"})
        securities = self._h30533_aliases(
            securities, {"index_pe": "pe", "source": "data_source"})
        constituent_columns = [
            "snapshot_date", "index_code", "security_code", "name", "exchange", "weight",
            "issuer_id", "security_type", "ads_ratio", "is_duplicate_issuer", "source_type", "as_of",
            "constituent_as_of", "raw_metadata", "validity_reason",
        ]
        daily_columns = [
            "trade_date", "index_code", "pe_ratio", "index_pe", "earnings_yield",
            "total_market_cap", "total_profit_ttm", "stock_count", "valid_count",
            "weight_coverage", "negative_profit_count", "missing_count", "invalid_count", "source_type",
            "source_date", "constituent_as_of", "constituent_basis", "validity_reason",
            "pe_mean", "pe_std", "pe_high_line", "pe_low_line", "pe_percentile",
            "updated_at",
        ]
        security_columns = [
            "snapshot_date", "index_code", "security_code", "name", "issuer_id", "weight",
            "price", "currency", "market_cap", "verified_shares", "profit_ttm",
            "earnings_yield", "index_pe", "pe", "financial_as_of", "source_date",
            "source_type", "source", "data_source", "validity_reason",
        ]
        constituent_date = constituents.iloc[0].get("as_of") or daily.iloc[0].get("trade_date")
        security_date = securities.iloc[0].get("source_date") or daily.iloc[0].get("trade_date")

        def records_for(frame, columns, default_date=None):
            records = []
            for _, row in frame.iterrows():
                values = []
                for column in columns:
                    if column == "index_code":
                        value = index_code
                    elif column == "snapshot_date" and default_date is not None:
                        value = row.get(column) or default_date
                    elif column == "updated_at":
                        value = time.time()
                    else:
                        value = self._h30533_value(row, column)
                    if column in ("security_code", "trade_date", "snapshot_date") and not value:
                        raise ValueError(f"missing H30533 {column}")
                    values.append(value)
                records.append(tuple(values))
            return records

        constituent_records = records_for(constituents, constituent_columns, constituent_date)
        daily_records = records_for(daily, daily_columns)
        security_records = records_for(securities, security_columns, security_date)
        with self._conn() as conn:
            for table in ("h30533_constituent_snapshot", "h30533_daily_valuation",
                          "h30533_security_valuation"):
                conn.execute(f"DELETE FROM {table} WHERE index_code=?", (index_code,))
            self._h30533_insert(conn, "h30533_constituent_snapshot", constituent_columns,
                                constituent_records)
            self._h30533_insert(conn, "h30533_daily_valuation", daily_columns, daily_records)
            self._h30533_insert(conn, "h30533_security_valuation", security_columns,
                                security_records)

    def load_h30533_snapshot(self, index_code: str):
        with self._conn() as conn:
            result = {}
            for key, table, order in (
                ("constituents", "h30533_constituent_snapshot", "snapshot_date, security_code"),
                ("daily", "h30533_daily_valuation", "trade_date"),
                ("securities", "h30533_security_valuation", "snapshot_date, security_code"),
            ):
                result[key] = pd.read_sql_query(
                    f"SELECT * FROM {table} WHERE index_code=? ORDER BY {order}",
                    conn, params=[index_code],
                )
            if not result["constituents"].empty:
                result["constituents"]["is_duplicate_issuer"] = (
                    result["constituents"]["is_duplicate_issuer"].fillna(False).astype(bool)
                )
                result["constituents"]["raw_metadata"] = result["constituents"]["raw_metadata"].apply(
                    lambda value: json.loads(value) if isinstance(value, str) else value
                )
            return result

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

    def save_us_pe_daily(self, pe_df: pd.DataFrame):
        """保存中概互联网每日 PE 序列"""
        if pe_df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM us_pe_daily")
            records = []
            for _, row in pe_df.iterrows():
                records.append((
                    row["trade_date"],
                    row.get("pe_ratio"),
                    row.get("total_market_cap"),
                    row.get("stock_count"),
                    time.time(),
                ))
            conn.executemany(
                """INSERT INTO us_pe_daily
                   (trade_date, pe_ratio, total_market_cap, stock_count, updated_at)
                   VALUES (?,?,?,?,?)""",
                records,
            )

    def load_us_pe_daily(self) -> pd.DataFrame:
        """加载中概互联网每日 PE 序列"""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM us_pe_daily ORDER BY trade_date", conn
            )

    def save_us_pe_snapshot(self, date: str, df: pd.DataFrame):
        """保存中概互联网当日指数 PE 快照"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM us_pe_daily WHERE trade_date=?", (date,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    row["trade_date"],
                    row.get("pe_ratio"),
                    row.get("total_market_cap"),
                    row.get("stock_count"),
                    time.time(),
                ))
            conn.executemany(
                """INSERT INTO us_pe_daily
                   (trade_date, pe_ratio, total_market_cap, stock_count, updated_at)
                   VALUES (?,?,?,?,?)""",
                records,
            )

    def load_us_pe_snapshot(self) -> pd.DataFrame:
        """加载最近一次中概互联网指数 PE 快照"""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM us_pe_daily ORDER BY trade_date DESC LIMIT 1", conn
            )

    def save_us_pe_stats(self, stats_df: pd.DataFrame):
        """保存中概互联网统计指标"""
        if stats_df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM us_pe_stats")
            records = []
            for _, row in stats_df.iterrows():
                records.append((
                    row["trade_date"],
                    row.get("pe_mean"),
                    row.get("pe_std"),
                    row.get("pe_high_line"),
                    row.get("pe_low_line"),
                    row.get("pe_percentile"),
                    time.time(),
                ))
            conn.executemany(
                """INSERT INTO us_pe_stats
                   (trade_date, pe_mean, pe_std, pe_high_line,
                    pe_low_line, pe_percentile, updated_at)
                   VALUES (?,?,?,?,?,?,?)""",
                records,
            )

    def load_us_pe_stats(self) -> pd.DataFrame:
        """加载中概互联网统计指标"""
        with self._conn() as conn:
            return pd.read_sql_query(
                "SELECT * FROM us_pe_stats ORDER BY trade_date", conn
            )

    def save_us_stock_pe(self, date: str, df: pd.DataFrame):
        """保存中概股单只股票 PE 快照"""
        if df.empty:
            return
        with self._conn() as conn:
            conn.execute("DELETE FROM us_stock_pe WHERE snapshot_date=?", (date,))
            records = []
            for _, row in df.iterrows():
                records.append((
                    date,
                    row["code"],
                    row.get("name", ""),
                    row.get("price"),
                    row.get("eps_ttm_cny"),
                    row.get("eps_ttm_usd"),
                    row.get("pe"),
                    time.time(),
                ))
            conn.executemany(
                """INSERT INTO us_stock_pe
                   (snapshot_date, code, name, price, eps_ttm_cny,
                    eps_ttm_usd, pe, updated_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                records,
            )

    def load_us_stock_pe(self) -> pd.DataFrame:
        """加载最新一期的中概股 PE 快照"""
        with self._conn() as conn:
            latest = conn.execute(
                "SELECT MAX(snapshot_date) FROM us_stock_pe"
            ).fetchone()
            if not latest or not latest[0]:
                return pd.DataFrame()
            df = pd.read_sql_query(
                "SELECT * FROM us_stock_pe WHERE snapshot_date=?",
                conn, params=[latest[0]]
            )
            return df

    def get_us_last_updated(self) -> Optional[str]:
        """获取中概股最近更新日期"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT MAX(trade_date) FROM us_pe_daily"
            ).fetchone()
            return row[0] if row and row[0] else None
