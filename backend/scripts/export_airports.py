"""Derive backend/app/data/airports.json from the frontend's airport dataset.

frontend/src/lib/airports-data.ts is the single source of truth (it feeds the
From/To autocomplete); the backend reads the JSON this script writes, so the two
never disagree about which airports exist. Re-run after editing the .ts file:

    cd backend && uv run python scripts/export_airports.py          # rewrite the JSON
    cd backend && uv run python scripts/export_airports.py --check  # exit 1 if stale

tests/test_normalize.py runs the --check comparison, so a stale JSON fails CI.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "frontend" / "src" / "lib" / "airports-data.ts"
TARGET = ROOT / "backend" / "app" / "data" / "airports.json"


def _block(text: str, name: str) -> list[str]:
    m = re.search(rf"export const {name} = `(.*?)`;", text, re.S)
    if not m:
        raise SystemExit(f"{SOURCE}: no {name} block")
    return [line.strip() for line in m.group(1).splitlines() if line.strip()]


def _aliases(raw: str) -> list[str]:
    return [a.strip() for a in raw.split(",") if a.strip()]


def build(text: str) -> dict:
    countries_src = re.search(r"export const COUNTRIES[^=]*=\s*\{(.*?)\};", text, re.S)
    if not countries_src:
        raise SystemExit(f"{SOURCE}: no COUNTRIES object")
    countries = {}
    for code, value in re.findall(r"\b([A-Z]{2}):\s*\"([^\"]*)\"", countries_src.group(1)):
        name, _, aliases = value.partition("|")
        countries[code] = {"name": name, "aliases": _aliases(aliases)}

    airports = []
    for line in _block(text, "AIRPORTS"):
        top = line.startswith("*")
        code, city, name, country, *rest = line.lstrip("*").split("|")
        airports.append({"code": code, "city": city, "name": name, "country": country,
                         "aliases": _aliases(rest[0] if rest else ""), "top": top})

    metros = []
    for line in _block(text, "METROS"):
        code, city, country, members, *rest = line.split("|")
        metros.append({"code": code, "city": city, "country": country, "members": members.split(),
                       "aliases": _aliases(rest[0] if rest else "")})
    return {"source": "frontend/src/lib/airports-data.ts", "countries": countries,
            "airports": airports, "metros": metros}


def render() -> str:
    return json.dumps(build(SOURCE.read_text(encoding="utf-8")), ensure_ascii=False, indent=1) + "\n"


def main(argv: list[str]) -> int:
    fresh = render()
    if "--check" in argv:
        current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
        if current != fresh:
            print(f"{TARGET} is stale: run scripts/export_airports.py", file=sys.stderr)
            return 1
        return 0
    TARGET.write_text(fresh, encoding="utf-8")
    data = json.loads(fresh)
    print(f"wrote {TARGET}: {len(data['airports'])} airports, {len(data['metros'])} metros, "
          f"{len(data['countries'])} countries")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
