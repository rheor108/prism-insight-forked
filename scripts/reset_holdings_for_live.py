"""
Archive and clear simulated holdings before starting LIVE trading.

stock_holdings / us_stock_holdings use ticker as PRIMARY KEY, so leftover demo
positions would block real buys of the same ticker. This archives them into
*_holdings_demo_archive and empties the live holdings tables. Trade history and
journals are preserved (they carry account_mode).

Usage:
    python scripts/reset_holdings_for_live.py --confirm
"""
import argparse
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "stock_tracking_db.sqlite"


def _archive_and_clear(cur, table):
    archive = f"{table}_demo_archive"
    cur.execute(f"CREATE TABLE IF NOT EXISTS {archive} AS SELECT * FROM {table} WHERE 0")
    cur.execute(f"INSERT INTO {archive} SELECT * FROM {table}")
    moved = cur.rowcount
    cur.execute(f"DELETE FROM {table}")
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--confirm", action="store_true", help="actually perform the reset")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()
    for table in ["stock_holdings", "us_stock_holdings"]:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            n = cur.fetchone()[0]
        except sqlite3.OperationalError:
            print(f"  {table}: not found, skipping")
            continue
        if not args.confirm:
            print(f"  {table}: {n} rows would be archived+cleared (dry-run)")
            continue
        moved = _archive_and_clear(cur, table)
        conn.commit()
        print(f"  {table}: archived {moved} rows -> {table}_demo_archive, cleared")
    conn.close()
    if not args.confirm:
        print("\nDry-run only. Re-run with --confirm to apply.")


if __name__ == "__main__":
    main()
