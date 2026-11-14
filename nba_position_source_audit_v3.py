#!/usr/bin/env python3
"""NBA LOCKBOX V3: auditable two-source position reconciliation.

Runs V2 to refresh ESPN roster labels, then compares against an OPTIONAL
independently sourced CSV at research_inputs/independent_player_positions.csv.
Never converts G/F into PG/SG/SF/PF, and never calls listed position an
on-court defensive assignment.
"""
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import nba_position_source_audit_v2 as v2

OUT = Path("research_exports")
SECONDARY = Path("research_inputs/independent_player_positions.csv")
VALID = {"PG", "SG", "SF", "PF", "C"}
REQUIRED = {"player_id", "position", "source_name", "source_url"}

def read_csv(path):
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))

def main():
    v2.main()
    primary = read_csv(OUT / "player_positions_audit_v2.csv")
    secondary = {}
    issues = []
    if SECONDARY.exists():
        rows = read_csv(SECONDARY)
        if rows and not REQUIRED.issubset(rows[0]):
            raise ValueError("Independent CSV requires: " + ", ".join(sorted(REQUIRED)))
        for i, row in enumerate(rows, 2):
            pid = (row.get("player_id") or "").strip()
            team = (row.get("team_id") or "").strip()
            pos = (row.get("position") or "").strip().upper()
            source = (row.get("source_name") or "").strip()
            url = (row.get("source_url") or "").strip()
            if not pid or pos not in VALID or not source or not url.startswith("https://"):
                issues.append(f"Row {i}: invalid ID, exact position, source, or URL")
                continue
            key = (pid, team)
            if key in secondary:
                issues.append(f"Row {i}: duplicate player/team key {key}")
                continue
            secondary[key] = row
    result = []
    for row in primary:
        pid, team = row["player_id"], row["team_id"]
        alt = secondary.get((pid, team)) or secondary.get((pid, ""))
        espn = row["exact_pg_sg_sf_pf_c"]
        other = (alt.get("position") or "").strip().upper() if alt else ""
        if espn and other:
            status = "exact_agreement" if espn == other else "source_conflict"
        elif other:
            status = "secondary_exact_only"
        elif espn:
            status = "espn_exact_only"
        else:
            status = "unresolved"
        # Only an exact agreement can be treated as two-source corroboration.
        result.append({
            **row,
            "independent_position": other,
            "independent_source_name": (alt.get("source_name") or "") if alt else "",
            "independent_source_url": (alt.get("source_url") or "") if alt else "",
            "comparison_status": status,
            "two_source_verified_position": espn if status == "exact_agreement" else "",
            "listed_position_not_on_court_role": "true",
        })
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / "player_positions_comparison_v3.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "player_team_pairs": len(result),
        "independent_source_file_present": SECONDARY.exists(),
        "independent_source_rows_accepted": len(secondary),
        "counts": dict(Counter(r["comparison_status"] for r in result)),
        "input_issues": issues,
        "limitations": [
            "An independent CSV is not automatically fetched; no secondary verification is claimed without it.",
            "Listed roster position does not prove the role played in a specific game.",
            "Five-position defensive allowances must not be generated from unresolved or conflicting labels.",
        ],
    }
    (OUT / "player_position_comparison_v3.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2))
    if issues:
        raise SystemExit("Independent position input has errors; inspect comparison report")

if __name__ == "__main__":
    main()
