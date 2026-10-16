"""Validate the static JSON publication contract."""
import json
import math
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
OUT = BASE / "static" / "data"


def _finite(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(value)
    return True


def validate_export(out=OUT):
    index_path = out / "index_list.json"
    manifest_path = out / "manifest.json"
    if not index_path.exists() or not manifest_path.exists():
        raise ValueError("index_list.json and manifest.json are required")
    index_list = json.loads(index_path.read_text(encoding="utf-8"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    required = {"code", "name", "type", "data_file", "quality_level", "coverage",
                "financial_as_of", "validity"}
    for item in index_list:
        missing = required - set(item)
        if missing:
            raise ValueError(f"{item.get('code')}: missing {sorted(missing)}")
        data_path = out / item["data_file"]
        if not data_path.exists():
            raise ValueError(f"missing data file: {data_path.name}")
        rows = json.loads(data_path.read_text(encoding="utf-8"))
        for row in rows:
            if not isinstance(row, list) or not row or not all(_finite(v) for v in row):
                raise ValueError(f"invalid row in {data_path.name}")
    artifacts = set(manifest.get("artifacts", []))
    actual = {path.name for path in out.glob("*.json") if path.name != "manifest.json"}
    # Keep legacy H30533.json readable while the catalog uses CHINA_INTL.json.
    manifest_legacy = {"H30533.json"} if "H30533.json" in actual else set()
    artifacts.update(manifest_legacy)
    if artifacts != actual:
        raise ValueError(f"manifest artifacts differ: manifest={artifacts}, actual={actual}")
    return True


if __name__ == "__main__":
    try:
        validate_export()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"export validation failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print("static export valid")
