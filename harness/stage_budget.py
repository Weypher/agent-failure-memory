"""Nested stage limits on the existing global request ledger, atomically reserved."""
from pathlib import Path
import time

from .agent import BudgetStop
from .artifacts import canonical
from .budget import BudgetLedger, nusd


class StageBudgetLedger(BudgetLedger):
    def __init__(self, path, authorization, *, route, limits):
        if not Path(path).is_file():
            raise BudgetStop('Stage spending requires the existing global ledger')
        if not route or not limits or any(not k or nusd(v) <= 0 for k, v in limits.items()):
            raise ValueError('Nonempty route and positive stage limits required')
        super().__init__(path, authorization)
        self.route = route
        self.limits = {k: nusd(v) for k, v in limits.items()}
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS stage_budget_limits(name TEXT PRIMARY KEY, cap INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS stage_budget_routes(route TEXT PRIMARY KEY, scopes TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS stage_budget_calls(call_id TEXT NOT NULL, scope TEXT NOT NULL,
            PRIMARY KEY(call_id,scope));
        ''')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for name, cap in self.limits.items():
                self.db.execute('INSERT OR IGNORE INTO stage_budget_limits VALUES (?,?)', (name, cap))
                if self.db.execute('SELECT cap FROM stage_budget_limits WHERE name=?', (name,)).fetchone()[0] != cap:
                    raise BudgetStop('Stage cap changed; explicit amendment required')
            scopes = canonical(sorted(self.limits))
            self.db.execute('INSERT OR IGNORE INTO stage_budget_routes VALUES (?,?)', (route, scopes))
            if self.db.execute('SELECT scopes FROM stage_budget_routes WHERE route=?', (route,)).fetchone()[0] != scopes:
                raise BudgetStop('Stage route scopes changed')
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def reserve(self, call_id, maximum):
        if not call_id.startswith(self.route + '/'):
            raise BudgetStop('Request ID does not belong to configured stage route')
        if type(maximum) is not int or maximum < 0:
            raise ValueError('Invalid reservation')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM calls WHERE charged>reserved LIMIT 1').fetchone():
                raise BudgetStop('Unreconciled provider cost overrun; ledger is stopped')
            used = self.db.execute('SELECT COALESCE(SUM(COALESCE(charged,reserved)),0) FROM calls').fetchone()[0]
            if used + maximum > self.cap:
                raise BudgetStop('Hard budget cap reached')
            for scope, cap in self.limits.items():
                spent = self.db.execute('SELECT COALESCE(SUM(COALESCE(c.charged,c.reserved)),0) '
                    'FROM calls c JOIN stage_budget_calls s ON c.id=s.call_id WHERE s.scope=?', (scope,)).fetchone()[0]
                if spent + maximum > cap:
                    raise BudgetStop('Stage budget cap reached: ' + scope)
            self.db.execute("INSERT INTO calls VALUES (?,?,NULL,'reserved',NULL,?)", (call_id, maximum, time.time()))
            self.db.executemany('INSERT INTO stage_budget_calls VALUES (?,?)', [(call_id, s) for s in self.limits])
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
