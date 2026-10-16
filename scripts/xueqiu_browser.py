"""Fetch Xueqiu company pages with an isolated, persistent Playwright session.

First run:
    python scripts/xueqiu_browser.py --login

Later runs reuse the browser profile at ``.xueqiu-browser``. The profile is
ignored by Git and no cookies or credentials are printed or exported.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".xueqiu-browser"
DEFAULT_SYMBOLS = ("BABA", "PDD", "TAL", "TME", "YMM")


def _parse_number(value: str | None) -> float | None:
    if not value:
        return None
    text = value.replace(",", "").replace("%", "").strip()
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text)
    return float(match.group()) if match else None


def _extract(text: str, labels: tuple[str, ...]) -> float | None:
    for label in labels:
        match = re.search(rf"{re.escape(label)}[^\d-]*([-+]?\d[\d,.]*)", text, re.I)
        if match:
            return _parse_number(match.group(1))
    return None


def fetch(symbols: list[str], login: bool = False) -> list[dict]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: pip install -r requirements.txt "
            "&& python -m playwright install chromium"
        ) from exc

    PROFILE.mkdir(parents=True, exist_ok=True)
    rows = []
    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(PROFILE), headless=not login, viewport={"width": 1440, "height": 1000}
        )
        page = context.new_page()
        for symbol in symbols:
            url = f"https://xueqiu.com/S/{symbol.upper()}"
            page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            page.wait_for_timeout(2_000)
            if login and symbol == symbols[0]:
                page.wait_for_timeout(1_000)
                input("Log in in the browser window, then press Enter here to continue: ")
            text = page.locator("body").inner_text(timeout=15_000)
            rows.append({
                "symbol": symbol.upper(),
                "source": "xueqiu_browser",
                "source_url": page.url,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "response_hash": hashlib.sha256(text.encode()).hexdigest(),
                "price": _extract(text, ("当前价", "现价", "最新价")),
                "market_cap": _extract(text, ("总市值", "市值")),
                "shares": _extract(text, ("总股本", "流通股")),
                "eps": _extract(text, ("每股收益", "EPS")),
                "pe": _extract(text, ("市盈率", "PE")),
                "raw_fields_found": {
                    "price": any(x in text for x in ("当前价", "现价", "最新价")),
                    "market_cap": "市值" in text,
                    "shares": any(x in text for x in ("总股本", "流通股")),
                    "eps": any(x in text for x in ("每股收益", "EPS")),
                    "pe": any(x in text for x in ("市盈率", "PE")),
                },
            })
        context.close()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--login", action="store_true", help="show browser and wait for login")
    parser.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    parser.add_argument("--output", type=Path, help="optional JSON output path")
    args = parser.parse_args()
    symbols = [item.strip().upper() for item in args.symbols.split(",") if item.strip()]
    rows = fetch(symbols, login=args.login)
    payload = json.dumps(rows, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
        print(f"Wrote {len(rows)} rows to {args.output}")
    else:
        print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
