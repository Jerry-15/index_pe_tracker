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
                    quality_level TEXT,
                    data_source TEXT,
                    generated_at TEXT,
                    coverage_ratio REAL,
                    financial_stale_count INTEGER,
                    validity_reason TEXT,
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
                    quality_level TEXT,
                    data_source TEXT,
                    generated_at TEXT,
                    coverage_ratio REAL,
                    financial_stale_count INTEGER,
                    validity_reason TEXT,
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
                    total_profit_ttm REAL,
                    stock_count INTEGER,
                    quality_level TEXT, financial_stale_count INTEGER, derived_market_cap_count INTEGER,
                    quote_disagreement_count INTEGER, excluded_count INTEGER, invalid_count INTEGER,
                    source_type TEXT, market_cap_method TEXT, profit_currency TEXT,
                    market_cap_currency TEXT, fx_rate REAL, normalized_profit_ttm REAL,
                    financial_fresh INTEGER, quote_source_count INTEGER, quote_disagreement INTEGER,
                    source_url TEXT, fetched_at TEXT, response_hash TEXT, current_constituents_backfill INTEGER,
                    current_constituents_caveat TEXT, updated_at REAL,
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
            self._migrate_regular_pe_tables(conn)
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS h30533_constituent_snapshot (
                    snapshot_date TEXT NOT NULL, index_code TEXT NOT NULL, security_code TEXT NOT NULL,
                    name TEXT, exchange TEXT, weight REAL, issuer_id TEXT, security_type TEXT,
                    ads_ratio REAL, source_type TEXT, as_of TEXT, constituent_as_of TEXT,
                    is_duplicate_issuer INTEGER,
                    raw_metadata TEXT, validity_reason TEXT,
                     source_url TEXT, fetched_at TEXT, response_hash TEXT,
                     quality_level TEXT, financial_stale_count INTEGER, derived_market_cap_count INTEGER,
                     quote_disagreement_count INTEGER, excluded_count INTEGER, market_cap_method TEXT,
                     profit_currency TEXT, market_cap_currency TEXT, fx_rate REAL,
                     normalized_profit_ttm REAL, financial_fresh INTEGER, quote_source_count INTEGER,
                     quote_disagreement INTEGER, current_constituents_caveat TEXT,
                     PRIMARY KEY (snapshot_date, index_code, security_code)
                );
                CREATE TABLE IF NOT EXISTS h30533_daily_valuation (
                    trade_date TEXT NOT NULL, index_code TEXT NOT NULL, pe_ratio REAL, index_pe REAL,
                    earnings_yield REAL, total_market_cap REAL, total_profit_ttm REAL,
                    stock_count INTEGER, valid_count INTEGER, weight_coverage REAL,
                    negative_profit_count INTEGER, missing_count INTEGER, source_type TEXT,
                    source_date TEXT, constituent_as_of TEXT, constituent_basis TEXT,
                    validity_reason TEXT, quality_level TEXT, financial_stale_count INTEGER,
                    derived_market_cap_count INTEGER, quote_disagreement_count INTEGER,
                    excluded_count INTEGER, invalid_count INTEGER, source_url TEXT,
                    fetched_at TEXT, response_hash TEXT, current_constituents_backfill INTEGER,
                     current_constituents_caveat TEXT, updated_at REAL,
                     market_cap_method TEXT, profit_currency TEXT, market_cap_currency TEXT,
                     fx_rate REAL, normalized_profit_ttm REAL, financial_fresh INTEGER,
                     quote_source_count INTEGER, quote_disagreement INTEGER,
                    PRIMARY KEY (trade_date, index_code)
                );
                CREATE TABLE IF NOT EXISTS h30533_security_valuation (
                    snapshot_date TEXT NOT NULL, index_code TEXT NOT NULL, security_code TEXT NOT NULL,
                    name TEXT, issuer_id TEXT, weight REAL, price REAL, currency TEXT,
                    market_cap REAL, verified_shares REAL, profit_ttm REAL, earnings_yield REAL,
                    index_pe REAL, pe REAL, financial_as_of TEXT, source_date TEXT,
                    source_type TEXT, source TEXT, data_source TEXT, validity_reason TEXT,
                    quality_level TEXT, market_cap_method TEXT, quote_source_count INTEGER,
                    quote_disagreement INTEGER, profit_currency TEXT, market_cap_currency TEXT,
                    fx_rate REAL, normalized_profit_ttm REAL, source_url TEXT, fetched_at TEXT,
                    response_hash TEXT, current_constituents_backfill INTEGER,
                     current_constituents_caveat TEXT, financial_fresh INTEGER,
                    PRIMARY KEY (snapshot_date, index_code, security_code)
                );
            """)
            self._migrate_h30533_tables(conn)

    @staticmethod
    def _migrate_regular_pe_tables(conn: sqlite3.Connection):
        migrations = {
            "index_pe_daily": {
                "quality_level": "TEXT", "data_source": "TEXT", "generated_at": "TEXT",
                "coverage_ratio": "REAL", "financial_stale_count": "INTEGER",
                "validity_reason": "TEXT",
            },
            "index_pe_stats": {
                "quality_level": "TEXT", "data_source": "TEXT", "generated_at": "TEXT",
                "coverage_ratio": "REAL", "financial_stale_count": "INTEGER",
                "validity_reason": "TEXT",
            },
            "us_pe_daily": {
                "quality_level": "TEXT", "financial_stale_count": "INTEGER",
                "derived_market_cap_count": "INTEGER", "quote_disagreement_count": "INTEGER",
                "excluded_count": "INTEGER", "invalid_count": "INTEGER", "source_type": "TEXT",
                "market_cap_method": "TEXT", "profit_currency": "TEXT", "market_cap_currency": "TEXT",
                "fx_rate": "REAL", "normalized_profit_ttm": "REAL", "financial_fresh": "INTEGER",
                "quote_source_count": "INTEGER", "quote_disagreement": "INTEGER", "source_url": "TEXT",
                "fetched_at": "TEXT", "response_hash": "TEXT", "current_constituents_backfill": "INTEGER",
                "current_constituents_caveat": "TEXT",
            },
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info([{table}])")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE [{table}] ADD COLUMN [{column}] {definition}")

    @staticmethod
    def _migrate_h30533_tables(conn: sqlite3.Connection):
        """Add allowlisted H30533 columns without disturbing legacy extensions."""
        evidence = {
            "quality_level": "TEXT", "financial_stale_count": "INTEGER",
            "derived_market_cap_count": "INTEGER", "quote_disagreement_count": "INTEGER",
            "excluded_count": "INTEGER", "invalid_count": "INTEGER", "source_url": "TEXT",
            "fetched_at": "TEXT", "response_hash": "TEXT", "market_cap_method": "TEXT",
            "profit_currency": "TEXT", "market_cap_currency": "TEXT", "fx_rate": "REAL",
            "normalized_profit_ttm": "REAL", "financial_fresh": "INTEGER",
            "quote_source_count": "INTEGER", "quote_disagreement": "INTEGER",
            "current_constituents_backfill": "INTEGER", "current_constituents_caveat": "TEXT",
        }
        migrations = {
            "h30533_constituent_snapshot": {
                "snapshot_date": "TEXT", "index_code": "TEXT", "security_code": "TEXT",
                "name": "TEXT", "exchange": "TEXT", "weight": "REAL", "issuer_id": "TEXT",
                "security_type": "TEXT", "ads_ratio": "REAL", "source_type": "TEXT",
                "as_of": "TEXT", "constituent_as_of": "TEXT", "is_duplicate_issuer": "INTEGER",
                "raw_metadata": "TEXT",
                "validity_reason": "TEXT",
                **evidence,
            },
            "h30533_daily_valuation": {
                "trade_date": "TEXT", "index_code": "TEXT", "pe_ratio": "REAL",
                "index_pe": "REAL", "earnings_yield": "REAL", "total_market_cap": "REAL",
                "total_profit_ttm": "REAL", "stock_count": "INTEGER", "valid_count": "INTEGER",
                "weight_coverage": "REAL", "negative_profit_count": "INTEGER",
                "missing_count": "INTEGER", "source_type": "TEXT", "source_date": "TEXT",
                "constituent_as_of": "TEXT", "constituent_basis": "TEXT",
                "validity_reason": "TEXT", "updated_at": "REAL",
                **evidence,
            },
            "h30533_security_valuation": {
                "snapshot_date": "TEXT", "index_code": "TEXT", "security_code": "TEXT",
                "name": "TEXT", "issuer_id": "TEXT", "weight": "REAL", "price": "REAL",
                "currency": "TEXT", "market_cap": "REAL", "verified_shares": "REAL",
                "profit_ttm": "REAL", "earnings_yield": "REAL", "index_pe": "REAL",
                "pe": "REAL", "financial_as_of": "TEXT", "source_date": "TEXT",
                "source_type": "TEXT", "source": "TEXT", "data_source": "TEXT",
                "validity_reason": "TEXT",
                **evidence,
            },
        }
        for table, columns in migrations.items():
            existing = {row[1] for row in conn.execute(f"PRAGMA table_info([{table}])")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE [{table}] ADD COLUMN [{column}] {definition}")

    @staticmethod
    def _h30533_value(row, column):
        value = row.get(column)
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=True, sort_keys=True)
        return None if pd.isna(value) else value

    def _save_h30533_table(self, conn, table, frame):
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info([{table}])")}
        preexisting = set(existing)
        allowlisted = {
            "h30533_constituent_snapshot": {
                "snapshot_date", "index_code", "security_code", "name", "exchange", "weight",
                "issuer_id", "security_type", "ads_ratio", "source_type", "as_of",
                "constituent_as_of", "is_duplicate_issuer", "raw_metadata", "validity_reason",
                 "source_url", "fetched_at", "response_hash", "current_constituents_backfill",
                 "current_constituents_caveat", "quality_level", "financial_stale_count",
                  "derived_market_cap_count", "quote_disagreement_count", "excluded_count", "invalid_count",
                 "market_cap_method", "profit_currency", "market_cap_currency", "fx_rate",
                 "normalized_profit_ttm", "financial_fresh", "quote_source_count", "quote_disagreement",
            },
            "h30533_daily_valuation": {
                "trade_date", "index_code", "pe_ratio", "index_pe", "earnings_yield",
                "total_market_cap", "total_profit_ttm", "stock_count", "valid_count",
                "weight_coverage", "negative_profit_count", "missing_count", "source_type",
                "source_date", "constituent_as_of", "constituent_basis", "validity_reason",
                "quality_level", "financial_stale_count", "derived_market_cap_count",
                "quote_disagreement_count", "excluded_count", "invalid_count", "source_url",
                 "fetched_at", "response_hash", "current_constituents_backfill",
                 "current_constituents_caveat", "updated_at", "market_cap_method", "profit_currency",
                 "market_cap_currency", "fx_rate", "normalized_profit_ttm", "financial_fresh",
                 "quote_source_count", "quote_disagreement",
            },
            "h30533_security_valuation": {
                "snapshot_date", "index_code", "security_code", "name", "issuer_id", "weight",
                "price", "currency", "market_cap", "verified_shares", "profit_ttm",
                "earnings_yield", "index_pe", "pe", "financial_as_of", "source_date",
                "source_type", "source", "data_source", "validity_reason", "quality_level",
                "market_cap_method", "quote_source_count", "quote_disagreement",
                "profit_currency", "market_cap_currency", "fx_rate", "normalized_profit_ttm",
                 "source_url", "fetched_at", "response_hash", "current_constituents_backfill",
                   "current_constituents_caveat", "financial_fresh", "financial_stale_count",
                   "derived_market_cap_count", "quote_disagreement_count", "excluded_count", "invalid_count",
            },
        }[table]
        for column in frame.columns:
            if column not in existing and column.isidentifier() and column.startswith("extra_"):
                conn.execute(f"ALTER TABLE [{table}] ADD COLUMN [{column}] TEXT")
                existing.add(column)
        # Core columns are explicit; known legacy columns and extra_* extensions remain writable.
        legacy_columns = {
            column for column in frame.columns
            if column in preexisting or column.startswith("extra_")
        }
        columns = [column for column in frame.columns
                   if (column in allowlisted or column in legacy_columns) and column.isidentifier()]
        names = ", ".join(f"[{column}]" for column in columns)
        placeholders = ",".join("?" for _ in columns)
        conn.executemany(
            f"INSERT OR REPLACE INTO {table} ({names}) VALUES ({placeholders})",
            [tuple(self._h30533_value(row, column) for column in columns)
             for _, row in frame.iterrows()],
        )

    @staticmethod
    def _h30533_aliases(frame, aliases):
        frame = frame.copy()
        for canonical, alias in aliases.items():
            if canonical not in frame and alias in frame:
                frame[canonical] = frame[alias]
        return frame

    def save_h30533_snapshot(self, index_code, constituents, daily, securities):
        if constituents.empty or daily.empty or securities.empty:
            raise ValueError("H30533 snapshot parts must not be empty")
        constituents = self._h30533_aliases(constituents, {"as_of": "constituent_as_of"})
        if "as_of" in constituents and "constituent_as_of" in constituents:
            as_of = constituents["as_of"].astype("string")
            constituent_as_of = constituents["constituent_as_of"].astype("string")
            if as_of.fillna(constituent_as_of).ne(constituent_as_of.fillna(as_of)).any():
                raise ValueError("H30533 constituent_as_of conflicts with as_of")
        daily = self._h30533_aliases(daily, {"pe_ratio": "index_pe", "stock_count": "valid_count"})
        securities = self._h30533_aliases(securities, {"pe": "index_pe", "data_source": "source"})
        frames = (constituents, daily, securities)
        required = (("security_code", "weight", "source_type", "as_of"),
                    ("trade_date", "source_type", "source_date", "constituent_as_of"),
                    ("security_code", "weight", "source_type", "source_date"))
        for frame, fields in zip(frames, required):
            if ("index_code" not in frame or frame["index_code"].isna().any() or
                    frame["index_code"].astype(str).ne(index_code).any()):
                raise ValueError("H30533 index_code mismatch")
            if any(field not in frame.columns for field in fields):
                raise ValueError("H30533 required metadata missing")
        constituent_dates = set(constituents["as_of"].dropna().astype(str))
        daily_constituent_dates = set(daily["constituent_as_of"].dropna().astype(str))
        if len(constituent_dates) != 1 or daily_constituent_dates != constituent_dates:
            raise ValueError("H30533 constituent_as_of mismatch")
        security_dates = set(securities["source_date"].dropna().astype(str))
        if len(security_dates) != 1:
            raise ValueError("H30533 security source_date mismatch")
        constituents = constituents.copy()
        daily = daily.copy()
        securities = securities.copy()
        frames = (constituents, daily, securities)
        for frame in frames:
            frame["index_code"] = index_code
        for frame in frames:
            if "validity_reason" not in frame:
                frame["validity_reason"] = None
        # The source response is one snapshot-level fact. Preserve it on every
        # frame so downstream exports do not have to infer provenance.
        provenance = ("source_type", "source_url", "fetched_at", "response_hash",
                      "current_constituents_backfill", "current_constituents_caveat")
        for column in provenance:
            if column not in constituents:
                continue
            value = constituents[column].dropna().iloc[0] if constituents[column].notna().any() else None
            for frame in (daily, securities):
                if column not in frame:
                    frame[column] = value
                else:
                    frame[column] = frame[column].where(frame[column].notna(), value)
        constituents["snapshot_date"] = constituents["as_of"]
        constituents["constituent_as_of"] = constituents["as_of"]
        securities["snapshot_date"] = securities["source_date"]
        with self._conn() as conn:
            for table in ("h30533_constituent_snapshot", "h30533_daily_valuation", "h30533_security_valuation"):
                conn.execute(f"DELETE FROM {table} WHERE index_code=?", (index_code,))
            self._save_h30533_table(conn, "h30533_constituent_snapshot", constituents)
            self._save_h30533_table(conn, "h30533_daily_valuation", daily)
            self._save_h30533_table(conn, "h30533_security_valuation", securities)

    def load_h30533_snapshot(self, index_code):
        with self._conn() as conn:
            result = {
                key: pd.read_sql_query(
                    f"SELECT * FROM {table} WHERE index_code=? ORDER BY {order}", conn, params=[index_code])
                for key, table, order in (
                    ("constituents", "h30533_constituent_snapshot", "snapshot_date, security_code"),
                    ("daily", "h30533_daily_valuation", "trade_date"),
                    ("securities", "h30533_security_valuation", "snapshot_date, security_code"),
                )
            }
        if "raw_metadata" in result["constituents"]:
            def decode_metadata(value):
                if not isinstance(value, str):
                    return value
                try:
                    return json.loads(value)
                except (TypeError, ValueError):
                    return value

            result["constituents"]["raw_metadata"] = result["constituents"]["raw_metadata"].apply(
                decode_metadata
            )
        return result

    def save_h30533_daily(self, index_code, df):
        if df.empty:
            return
        daily = self._h30533_aliases(df, {"pe_ratio": "index_pe", "stock_count": "valid_count"})
        required = {"trade_date", "source_type", "source_date", "constituent_as_of"}
        if "index_code" not in daily:
            daily = daily.copy()
            daily["index_code"] = index_code
        if daily["index_code"].astype(str).ne(index_code).any() or not required.issubset(daily.columns):
            raise ValueError("H30533 daily metadata missing or mismatched")
        daily = daily.copy()
        if "validity_reason" not in daily:
            daily["validity_reason"] = None
        with self._conn() as conn:
            conn.execute("DELETE FROM h30533_daily_valuation WHERE index_code=?", (index_code,))
            self._save_h30533_table(conn, "h30533_daily_valuation", daily)

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
            self._save_regular_pe_table(conn, "index_pe_daily", index_code, df)

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
            self._save_regular_pe_table(conn, "index_pe_stats", index_code, df)

    def _save_regular_pe_table(self, conn, table, index_code, df):
        """Replace an index's rows without losing omitted legacy columns."""
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info([{table}])")}
        columns = [column for column in df.columns if column in existing and column.isidentifier()]
        for column in ("trade_date", "index_code", "updated_at"):
            if column not in columns:
                columns.append(column)

        dates = [self._h30533_value(row, "trade_date") for _, row in df.iterrows()]
        placeholders = ",".join("?" for _ in dates)
        conn.execute(
            f"DELETE FROM [{table}] WHERE index_code=? AND trade_date NOT IN ({placeholders})",
            [index_code, *dates],
        )

        names = ", ".join(f"[{column}]" for column in columns)
        updates = [column for column in columns if column not in ("trade_date", "index_code")]
        if updates:
            assignments = ", ".join(f"[{column}]=excluded.[{column}]" for column in updates)
            conflict = f" ON CONFLICT(trade_date, index_code) DO UPDATE SET {assignments}"
        else:
            conflict = " ON CONFLICT(trade_date, index_code) DO NOTHING"
        records = [tuple(
            index_code if column == "index_code" else
            time.time() if column == "updated_at" else self._h30533_value(row, column)
            for column in columns
        ) for _, row in df.iterrows()]
        value_placeholders = ",".join("?" for _ in columns)
        conn.executemany(
            f"INSERT INTO [{table}] ({names}) VALUES ({value_placeholders})" + conflict,
            records,
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
                "SELECT MAX(trade_date) FROM h30533_daily_valuation WHERE index_code=?",
                (index_code,)
            ).fetchone()
            if index_code != "H30533" and not (row and row[0]):
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
            columns = (
                "trade_date", "pe_ratio", "total_market_cap", "total_profit_ttm", "stock_count",
                 "quality_level", "financial_stale_count", "derived_market_cap_count",
                 "quote_disagreement_count", "excluded_count", "source_type", "source_url",
                 "fetched_at", "response_hash", "current_constituents_backfill",
                 "current_constituents_caveat", "invalid_count", "market_cap_method",
                 "profit_currency", "market_cap_currency", "fx_rate", "normalized_profit_ttm",
                 "financial_fresh", "quote_source_count", "quote_disagreement", "updated_at",
            )
            records = []
            for _, row in pe_df.iterrows():
                values = [self._h30533_value(row, column) for column in columns[:-1]]
                records.append((*values, time.time()))
            conn.executemany(
                f"INSERT INTO us_pe_daily ({', '.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
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
            columns = (
                "trade_date", "pe_ratio", "total_market_cap", "total_profit_ttm", "stock_count",
                 "quality_level", "financial_stale_count", "derived_market_cap_count",
                 "quote_disagreement_count", "excluded_count", "source_type", "source_url",
                 "fetched_at", "response_hash", "current_constituents_backfill",
                 "current_constituents_caveat", "invalid_count", "market_cap_method",
                 "profit_currency", "market_cap_currency", "fx_rate", "normalized_profit_ttm",
                 "financial_fresh", "quote_source_count", "quote_disagreement", "updated_at",
            )
            records = []
            for _, row in df.iterrows():
                values = [self._h30533_value(row, column) for column in columns[:-1]]
                records.append((*values, time.time()))
            conn.executemany(
                f"INSERT INTO us_pe_daily ({', '.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
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
                "INSERT INTO us_pe_stats VALUES (?,?,?,?,?,?,?)", records
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
                    row.get("pb"),
                    time.time(),
                ))
            conn.executemany(
                "INSERT INTO us_stock_pe VALUES (?,?,?,?,?,?,?,?,?)", records
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
                "SELECT MAX(trade_date) FROM h30533_daily_valuation WHERE index_code=?",
                ("H30533",),
            ).fetchone()
            if row and row[0]:
                return row[0]
            row = conn.execute("SELECT MAX(trade_date) FROM us_pe_daily").fetchone()
            return row[0] if row and row[0] else None
