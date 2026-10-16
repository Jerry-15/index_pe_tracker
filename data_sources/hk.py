"""Hong Kong individual-stock valuation normalization."""
import pandas as pd


class HKDataSource:
    """Pure normalization helpers; network adapters can be added without mixing A-share data."""

    @staticmethod
    def build_snapshot(raw):
        price = float(raw["price"])
        revenue = float(raw["revenue"]) if raw.get("revenue") is not None else None
        profit = float(raw["net_profit"]) if raw.get("net_profit") is not None else None
        shares = float(raw["shares"]) if raw.get("shares") is not None else None
        market_cap = price * shares if shares and shares > 0 else None
        result = {
            "price": price,
            "revenue": revenue,
            "net_profit": profit,
            "eps": raw.get("eps"),
            "market_cap": market_cap,
            "market_cap_method": "estimated_shares" if market_cap is not None else "unavailable",
            "ps_ratio": market_cap / revenue if market_cap is not None and revenue and revenue > 0 else None,
            "pe_ratio": market_cap / profit if market_cap is not None and profit and profit > 0 else None,
            "currency": "HKD",
            "profit_status": "profitable" if profit is not None and profit > 0 else "loss-making",
            "valuation_metric": "PE" if profit is not None and profit > 0 else "PS",
            "data_as_of": raw.get("data_as_of"),
        }
        return result

    def get_stock_snapshot(self, code="02498"):
        raise RuntimeError(f"HK live source is not configured for {code}")
