"""Compare current H30533 valuation paths without changing production data."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data_sources.h30533 import H30533Source
from data_sources.us import USDataSource
from etl.pipeline_us import aggregate_h30533


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/h30533-valuation-comparison.json")
    args = parser.parse_args()
    snapshot = H30533Source().get_snapshot()
    source = USDataSource()
    records = []
    for _, item in snapshot.iterrows():
        record = source.get_current_pe(item["security_code"])
        record.update({
            "security_code": item["security_code"],
            "name": item["name"],
            "weight": item["weight"],
            "source_type": item["source_type"],
            "constituent_as_of": item["as_of"],
        })
        records.append(record)
    result = aggregate_h30533(__import__("pandas").DataFrame(records))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source_type": snapshot.iloc[0]["source_type"],
        "constituent_as_of": snapshot.iloc[0]["as_of"],
        "security_count": len(records),
        "aggregate": result,
        "records": records,
    }
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"output": args.output, **result}, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
