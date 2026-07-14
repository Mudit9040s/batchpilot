"""BatchPilot CLI.

Examples:
  python -m batchpilot.cli data.xlsx --profile demo --dry-run
  python -m batchpilot.cli data.xlsx --profile demo --ai --send --report out.xlsx
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ai_validate import ai_available, validate_with_ai
from .config import get_profile
from .ingest import read_rows
from .report import write_report
from .rules import validate_rows
from .sender import send_all


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="batchpilot", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", type=Path, help=".xlsx / .csv / .tsv input")
    ap.add_argument("--profile", required=True, help="profile key (profiles/<key>.yaml)")
    ap.add_argument("--ai", action="store_true", help="run AI semantic validation")
    ap.add_argument("--send", action="store_true", help="actually send to the API")
    ap.add_argument("--dry-run", action="store_true", help="print first batch payload and exit")
    ap.add_argument("--skip-errors", action="store_true",
                    help="hold back rows with validation errors when sending")
    ap.add_argument("--report", type=Path, help="write per-row .xlsx report here")
    args = ap.parse_args(argv)

    profile = get_profile(args.profile)
    headers, rows = read_rows(args.file, args.file.name)
    print(f"Loaded {len(rows)} rows from {args.file.name}")

    issues = validate_rows(profile, rows)
    if args.ai:
        if not ai_available():
            print("! ANTHROPIC_API_KEY not set — skipping AI validation")
        else:
            ai_issues = validate_with_ai(rows, profile.field_names or headers)
            if ai_issues:
                issues = [a + b for a, b in zip(issues, ai_issues)]

    err_rows = [i for i, lst in enumerate(issues) if any(x["severity"] == "error" for x in lst)]
    warn_rows = [i for i, lst in enumerate(issues)
                 if lst and i not in err_rows]
    print(f"Validation: {len(err_rows)} row(s) with errors, {len(warn_rows)} with warnings")
    for i in err_rows + warn_rows:
        for it in issues[i]:
            print(f"  row {i + 1}: [{it['severity']}] {it['message']}")

    if args.dry_run:
        batch = rows[:profile.batch_size]
        print(f"\nDry run — first batch payload ({len(batch)} records) for "
              f"{profile.method} {profile.endpoint}:")
        print(json.dumps({profile.records_key: batch}, indent=2, default=str))
        return 0

    outcomes = None
    if args.send:
        send_idx = [i for i in range(len(rows))
                    if not (args.skip_errors and i in err_rows)]
        outcomes = [{"status": "skipped", "message": "held back (validation errors)"}
                    for _ in rows]
        if send_idx:
            sent = send_all(profile, [rows[i] for i in send_idx],
                            progress_cb=lambda done, total: print(f"  sent {done}/{total}"))
            for pos, i in enumerate(send_idx):
                outcomes[i] = sent[pos]
        ok = sum(1 for o in outcomes if o["status"] == "success")
        failed = sum(1 for o in outcomes if o["status"] == "failed")
        print(f"Done: {ok} accepted, {failed} rejected, "
              f"{len(rows) - ok - failed} held back")

    if args.report:
        write_report(args.report, headers, rows, issues, outcomes)
        print(f"Report written: {args.report}")

    return 1 if (args.send and outcomes and
                 any(o["status"] == "failed" for o in outcomes)) else 0


if __name__ == "__main__":
    sys.exit(main())
