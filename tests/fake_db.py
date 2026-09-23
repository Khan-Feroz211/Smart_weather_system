"""
Test double for `db.connect()` -- lets the admin/auth code run without PostgreSQL.

It does NOT execute SQL.  It (1) asserts every statement has as many %s
placeholders as bound parameters, (2) refuses SQLite-isms, and (3) returns rows
whose columns are derived from the SELECT list so templates can be rendered.
Individual tests register `overrides` to script specific answers.
"""
import re

import db

TS = "2026-09-19 10:00:00"


def _split_top_level(text):
    parts, depth, cur = [], 0, []
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def _top_level_select_list(sql):
    m = re.search(r"\bSELECT\b", sql, re.I)
    if not m:
        return ""
    depth, i, start = 0, m.end(), m.end()
    while i < len(sql):
        ch = sql[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and re.match(r"\bFROM\b", sql[i:], re.I) and (i == 0 or not sql[i - 1].isalnum()):
            return sql[start:i]
        i += 1
    return sql[start:]


def select_columns(sql):
    text = _top_level_select_list(sql)
    if not text.strip():
        return []
    cols = []
    for item in _split_top_level(text):
        alias = re.search(r"\bAS\s+\"?(\w+)\"?\s*$", item, re.I)
        if alias:
            cols.append(alias.group(1))
        else:
            tail = re.findall(r"(\w+)\s*\)?\s*$", item)
            cols.append(tail[0] if tail else "col")
    return cols


def default_value(name):
    n = name.lower()
    if n in ("presence",):
        return "active"
    if n.startswith("is_") or n in ("has_admin_record", "is_live"):
        return True
    if n.endswith(("_at", "_date", "timestamp", "_time", "date")) or n in ("day",):
        return TS
    if n in ("role",):
        return "user"
    if n == "id":
        return 5
    if n in ("username", "owner", "farm", "name", "location", "email", "crop_type", "sensor_id", "status",
             "activity_type", "label", "action", "admin", "severity", "alert_type", "message", "sensor_type",
             "soil_type", "irrigation_type", "growth_stage", "owner_name", "quality_label", "weather_condition",
             "model_type", "experiment_name", "source", "data_source", "text", "when", "auth_user_id",
             "database", "version", "notes", "quality_flags", "target_type", "target_id"):
        return "sample"
    return 3


class FakeCursor:
    def __init__(self, cols, rows, rowcount=1):
        self._cols, self._rows, self.rowcount = cols, rows, rowcount
        self.description = [(c,) for c in cols]

    def fetchone(self):
        return db.Row(self._cols, self._rows[0]) if self._rows else None

    def fetchall(self):
        return [db.Row(self._cols, r) for r in self._rows]


class FakeConn:
    def __init__(self, log, overrides):
        self.log, self.overrides = log, overrides
        self.committed = False

    def execute(self, sql, params=None):
        params = list(params or [])
        placeholders = len(re.findall(r"(?<!%)%s", sql))
        assert placeholders == len(params), f"placeholder/param mismatch ({placeholders} vs {len(params)}): {sql[:120]}"
        assert "?" not in re.sub(r"'[^']*'", "", sql), f"SQLite placeholder left in: {sql[:120]}"
        assert "datetime('now" not in sql and "INSERT OR" not in sql, "SQLite syntax left in SQL"
        self.log.append((" ".join(sql.split()), params))
        for needle, answer in self.overrides:
            if needle in sql:
                value = answer(sql, params) if callable(answer) else answer
                if isinstance(value, FakeCursor):
                    return value
                cols, rows = value
                return FakeCursor(cols, rows)
        head = sql.lstrip().upper()
        if head.startswith(("UPDATE", "DELETE", "INSERT")) and "RETURNING" not in head:
            return FakeCursor([], [], rowcount=1)
        cols = select_columns(sql) or ["count"]
        if head.startswith("SELECT COUNT(*)") and len(cols) == 1:
            return FakeCursor(["count"], [(2,)])
        row = tuple(default_value(c) for c in cols)
        # list queries: two rows; single-value/aggregate queries: one row is enough
        return FakeCursor(cols, [row, row])

    def commit(self):
        self.committed = True

    def rollback(self):
        pass

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeDB:
    def __init__(self):
        self.log, self.overrides, self.down = [], [], False

    def connect(self):
        if self.down:
            raise db.DatabaseUnavailable("simulated outage")
        return FakeConn(self.log, self.overrides)

    def on(self, needle, cols, rows):
        self.overrides.insert(0, (needle, (cols, rows)))

    def statements(self, needle):
        return [s for s in self.log if needle in s[0]]
