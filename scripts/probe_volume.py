"""Dev probe: call DataForSEO search_volume and dump the raw response shape.

Run from the repo root:
    source .venv/bin/activate && python scripts/probe_volume.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Allow running as a script from repo root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.dataforseo import DataForSEOClient, _flatten_results, _first_result


TEST_KEYWORDS = [
    "cdmo environmental monitoring",
    "temperature mapping cdmo",
    "data logger calibration",
    "cleanroom monitoring",
    "humidity sensor",
    "pharma temperature monitoring",
    "gmp compliance",
    "cold chain monitoring",
]


async def main():
    print(f"\nTesting search_volume with {len(TEST_KEYWORDS)} keywords (US, English)\n")
    print("=" * 70)

    async with DataForSEOClient() as df:
        # Hit the endpoint directly so we can see the RAW response
        body = await df._post(
            "/v3/keywords_data/google_ads/search_volume/live",
            [{
                "keywords": TEST_KEYWORDS,
                "location_code": 2840,
                "language_code": "en",
            }],
        )

        # Top-level shape
        print(f"status_code: {body.get('status_code')}")
        print(f"tasks_count: {body.get('tasks_count')}")
        print(f"cost: ${body.get('cost', 0):.4f}")
        print()

        tasks = body.get("tasks", [])
        print(f"tasks[]: {len(tasks)} task(s)")
        if tasks:
            t0 = tasks[0]
            print(f"  task[0].status_code: {t0.get('status_code')}")
            print(f"  task[0].status_message: {t0.get('status_message')}")
            print(f"  task[0].result_count: {t0.get('result_count')}")
            results = t0.get("result", []) or []
            print(f"  task[0].result[]: {len(results)} entries")
            print()

            if results:
                print("First 3 entries shape:")
                for i, r in enumerate(results[:3]):
                    print(f"\n  --- result[{i}] ---")
                    print(json.dumps(r, indent=2)[:600])

        print()
        print("=" * 70)
        print("\nNormalization tests:\n")

        first = _first_result(body)
        print(f"_first_result() returns dict with keys: {list(first.keys()) if isinstance(first, dict) else type(first)}")
        items_via_first = first.get("items") if isinstance(first, dict) else None
        print(f"_first_result().get('items'): {len(items_via_first) if items_via_first else 'None / empty'}")

        flat = _flatten_results(body)
        print(f"_flatten_results(): {len(flat)} items")
        print(f"keywords mapped (sample): {[(x.get('keyword'), x.get('search_volume')) for x in flat[:5]]}")
        print()

        non_null = sum(1 for x in flat if x.get("search_volume") is not None)
        print(f"Keywords with non-null volume: {non_null}/{len(flat)}")


if __name__ == "__main__":
    asyncio.run(main())
