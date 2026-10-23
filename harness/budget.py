"""One durable global ledger; reservations survive interruption and never reset."""
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sqlite3
import time

from .agent import BudgetStop
from .artifacts import canonical, digest


def require_run_ledger(run_root, ledger_path):
    """A later study phase cannot silently restart this authorization's ledger."""
    import json
    expected={str(Path(json.loads(p.read_text())['budget_path']).resolve())
              for p in Path(run_root).glob('jobs/*/job.json')}
    actual=str(Path(ledger_path).resolve())
    if expected!={actual} or not Path(actual).is_file():
        raise BudgetStop('Study phases must share the existing pilot budget ledger; a new ledger is not new spending authorization')


def nusd(usd):
    return int((Decimal(str(usd)) * 1_000_000_000).to_integral_value(rounding=ROUND_CEILING))


class BudgetLedger:
    def __init__(self, path, authorization):
        if authorization.get("authorized_usd") is None or not authorization.get("authorization_evidence"):
            raise BudgetStop("Explicit monetary authorization is missing")
        if Decimal(str(authorization["authorized_usd"])) <= 0:
            raise BudgetStop("Positive monetary cap required")
        self.cap = nusd(authorization["authorized_usd"])
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=120)  # 30 -> 120 s: parallel workers on a 9p-mounted ledger (main r2 stop)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS meta (id INTEGER PRIMARY KEY CHECK(id=1), authorization TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS calls (id TEXT PRIMARY KEY, reserved INTEGER NOT NULL,
            charged INTEGER, status TEXT NOT NULL, usage TEXT, created REAL NOT NULL);
        """)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES (1,?)", (canonical(authorization),))
        if self.db.execute("SELECT authorization FROM meta").fetchone()[0] != canonical(authorization):
            raise BudgetStop("Authorization changed: use an explicit audited budget amendment")

    def reserve(self, call_id, maximum):
        if type(maximum) is not int or maximum < 0:
            raise ValueError("Negative reservation")
        self.db.execute("BEGIN IMMEDIATE")
        try:
            if self.db.execute('SELECT 1 FROM calls WHERE charged>reserved LIMIT 1').fetchone():
                raise BudgetStop('Unreconciled provider cost overrun; ledger is stopped')
            used = self.db.execute("SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls").fetchone()[0]
            if used + maximum > self.cap:
                raise BudgetStop("Hard budget cap reached")
            self.db.execute("INSERT INTO calls VALUES (?,?,NULL,'reserved',NULL,?)", (call_id, maximum, time.time()))
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def settle(self, call_id, actual, usage):
        if type(actual) is not int or actual < 0:raise ValueError('Invalid actual cost')
        with self.db:
            row = self.db.execute("SELECT reserved,status FROM calls WHERE id=?", (call_id,)).fetchone()
            if row is None or row[1] != "reserved":
                raise ValueError("Missing or settled reservation")
            self.db.execute("UPDATE calls SET charged=?,status='settled',usage=? WHERE id=?",
                            (actual, canonical(usage), call_id))
        if actual > row[0]:
            raise BudgetStop("Provider usage exceeded conservative reservation; reconcile prices before continuing")

    def unknown(self, call_id, category):
        with self.db:
            self.db.execute("UPDATE calls SET status=? WHERE id=? AND status='reserved'", (category, call_id))

    def summary(self):
        rows = self.db.execute("SELECT id,reserved,charged,status,usage FROM calls ORDER BY created").fetchall()
        return {"cap_nusd": self.cap, "known_nusd": sum(r[2] or 0 for r in rows),
                "uncertain_reserved_nusd": sum(r[1] for r in rows if r[2] is None),
                "requests": len(rows), "calls": [dict(zip(["id","reserved_nusd","charged_nusd","status","usage"], r)) for r in rows]}
