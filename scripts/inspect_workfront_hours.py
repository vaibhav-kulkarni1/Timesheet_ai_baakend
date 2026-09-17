"""
Diagnostic: dump the RAW Workfront response for one timesheet's hour entries,
so you can confirm the real field names before trusting the mapping in
workfront_client.py.

The field mapping (status values, hourType label, entryDate, etc.) in
RealWorkfrontClient is my best guess from Workfront's v21 docs — your
instance may name things slightly differently. Run this once, look at the
output, and tell me if any field is missing or named differently; I'll
adjust _STATUS_MAP / _map_task_type / the fields list accordingly.

Usage:
    # .env must have WORKFRONT_MODE=real + base URL + token
    python -m scripts.inspect_workfront_hours <timesheet_id>

    # e.g. Robin T's 42h timesheet from your sample:
    python -m scripts.inspect_workfront_hours 6a703fa400d6ffb6579493efcf0c1fa5
"""
from __future__ import annotations

import json
import sys

import httpx

from app.config import get_settings


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m scripts.inspect_workfront_hours <timesheet_id>")
        sys.exit(1)

    timesheet_id = sys.argv[1]
    settings = get_settings()

    if not settings.workfront_base_url or not settings.workfront_api_key:
        print("Set WORKFRONT_BASE_URL and WORKFRONT_API_KEY in .env first.")
        sys.exit(1)

    client = httpx.Client(
        base_url=settings.workfront_base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {settings.workfront_api_key}"},
        timeout=30.0,
    )

    print(f"=== Timesheet header {timesheet_id} ===")
    r = client.get(f"/tshet/{timesheet_id}", params={"fields": "*"})
    print("status:", r.status_code)
    print(json.dumps(r.json(), indent=2)[:4000])

    print(f"\n=== Hour entries for timesheet {timesheet_id} ===")
    # Try fields=* first to see EVERYTHING available on the HOUR object.
    r = client.get("/hour/search", params={"fields": "*", "timesheetID": timesheet_id, "$$LIMIT": 50})
    print("status:", r.status_code)
    data = r.json().get("data", [])
    print(f"returned {len(data)} hour rows")
    if data:
        print("\n--- first hour row, all fields ---")
        print(json.dumps(data[0], indent=2))
        print("\n--- field names present across rows ---")
        keys: set[str] = set()
        for row in data:
            keys.update(row.keys())
        print(sorted(keys))
    else:
        print("No hour rows. The timesheet may store hours differently, or the "
              "filter key isn't timesheetID on your instance. Try /hour/search?fields=*&$$LIMIT=5 "
              "to see a sample hour and its available filter fields.")


if __name__ == "__main__":
    main()
