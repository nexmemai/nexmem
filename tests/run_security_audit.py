"""
Task 6.3: Security audit script using bandit.
Run with: python tests/run_security_audit.py
Output is written to security_report.json.
"""
import subprocess
import sys
import json
from pathlib import Path


def run_bandit():
    print("=" * 60)
    print("Nexmem Security Audit — bandit SAST scanner")
    print("=" * 60)

    result = subprocess.run(
        [
            sys.executable, "-m", "bandit",
            "-r", "app",                      # scan the entire app directory
            "-f", "json",                     # JSON output for CI parsing
            "-o", "security_report.json",     # output file
            "--severity-level", "medium",     # only flag medium+ severity
            "-x", "app/demo_db.py",           # exclude demo stubs
        ],
        capture_output=True,
        text=True,
    )

    report_path = Path("security_report.json")
    if report_path.exists():
        with open(report_path) as f:
            report = json.load(f)

        issues = report.get("results", [])
        high = [i for i in issues if i["issue_severity"] == "HIGH"]
        medium = [i for i in issues if i["issue_severity"] == "MEDIUM"]

        print(f"\n  HIGH severity issues   : {len(high)}")
        print(f"  MEDIUM severity issues : {len(medium)}")
        print(f"\n  Full report written to : security_report.json")

        if high:
            print("\n  HIGH SEVERITY FINDINGS — must be resolved before deploy:")
            for issue in high:
                print(f"    [{issue['test_id']}] {issue['issue_text']}")
                print(f"      → {issue['filename']}:{issue['line_number']}")
            sys.exit(1)
        else:
            print("\n  No HIGH severity issues found. ✓")
    else:
        print("  bandit report not generated. Check installation.")
        print(result.stdout)
        print(result.stderr)
        sys.exit(1)


if __name__ == "__main__":
    run_bandit()
