#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
PostgreSQL store for the lead pipeline — Neon (Lakebase Postgres) backend.

Direct psycopg2 connection using DATABASE_URL (or DATABASE_URL_UNPOOLED).
Maintains identical signatures to the original store:
  - configured()
  - insert_rows(table, rows)
  - select_rows(table, columns, filters, filters_gte, limit, order)
  - update_rows(table, updates, filters)
  - upsert_rows(table, rows, on_conflict)
"""

import json
import os
import re
import sys

import psycopg2
import psycopg2.extras

# --------------------------------------------------------------------------- #
# .env loader
# --------------------------------------------------------------------------- #
def _load_dotenv(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            v = v.strip().strip('"').strip("'")
            os.environ.setdefault(k.strip(), v)

HERE = os.path.dirname(os.path.abspath(__file__))
_load_dotenv(os.path.join(HERE, ".env"))
_load_dotenv(os.path.join(os.path.dirname(HERE), ".env"))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

DATABASE_URL = (
    os.environ.get("DATABASE_URL")
    or os.environ.get("DATABASE_URL_UNPOOLED", "")
).strip()


def configured():
    """True when DATABASE_URL is set."""
    return bool(DATABASE_URL)


def _connect():
    """Create a new database connection with RealDictCursor."""
    if not configured():
        raise RuntimeError(
            "Database not configured. Add DATABASE_URL to .env."
        )
    return psycopg2.connect(
        DATABASE_URL, cursor_factory=psycopg2.extras.RealDictCursor
    )


def _serialize_val(val):
    """Serialize dicts/lists to JSON strings if needed for JSONB columns."""
    if isinstance(val, dict):
        return json.dumps(val, default=str)
    return val


def insert_rows(table, rows):
    """
    Insert one or many rows into `table`. Returns inserted rows.
    """
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return []

    conn = _connect()
    created = []
    try:
        with conn.cursor() as cur:
            for row in rows:
                cols = list(row.keys())
                placeholders = ["%s"] * len(cols)
                values = [_serialize_val(row[c]) for c in cols]
                sql = (
                    f"INSERT INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({', '.join(placeholders)}) RETURNING *"
                )
                cur.execute(sql, values)
                inserted = dict(cur.fetchone())
                created.append(inserted)
        conn.commit()
    finally:
        conn.close()
    return created


def _split_top_level_columns(col_str):
    """Split comma-separated columns ignoring commas within parentheses."""
    parts = []
    cur = []
    depth = 0
    for ch in col_str:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts


def _expand_columns(table, columns):
    """
    Parses column specs including nested embeds like:
      prospect_id(id,business_name,phone,...)
    Returns (select_clause, join_clause, alias_map).
    """
    if columns == "*":
        return f"{table}.*", "", {}

    parts = _split_top_level_columns(columns)
    select_items = []
    joins = []
    alias_map = {}

    for part in parts:
        m = re.match(r"^(\w+)\((.+)\)$", part)
        if m:
            fk_col, sub_spec = m.group(1), m.group(2)
            fk_table = fk_col[:-3] + "s" if fk_col.endswith("_id") else fk_col
            # Specific mappings
            if fk_col == "prospect_id":
                fk_table = "prospects"
            elif fk_col == "campaign_id":
                fk_table = "campaigns"

            sub_cols = [s.strip() for s in sub_spec.split(",")]
            alias_map[fk_col] = (fk_table, sub_cols)
            for sc in sub_cols:
                alias = f"_fk_{fk_col}_{sc}"
                select_items.append(f"{fk_table}.{sc} AS {alias}")
            joins.append(
                f"LEFT JOIN {fk_table} ON {table}.{fk_col} = {fk_table}.id"
            )
        else:
            select_items.append(f"{table}.{part}")

    return ", ".join(select_items), " ".join(joins), alias_map


def _renest(rows, alias_map):
    """Reconstruct nested dicts from _fk_<fk_col>_<sub_col> columns."""
    result = []
    for r in rows:
        row = dict(r)
        for fk_col, (fk_table, sub_cols) in alias_map.items():
            nested = {}
            for sc in sub_cols:
                alias = f"_fk_{fk_col}_{sc}"
                if alias in row:
                    nested[sc] = row.pop(alias)
            row[fk_col] = nested
        result.append(row)
    return result


def select_rows(
    table, columns="*", filters=None, filters_gte=None, limit=None, order=None
):
    """
    Select rows from `table`. Returns a list of dicts.
    """
    select_sql, join_sql, alias_map = _expand_columns(table, columns)
    clauses = []
    params = []

    if filters:
        for k, v in filters.items():
            clauses.append(f"{table}.{k} = %s")
            params.append(v)
    if filters_gte:
        for k, v in filters_gte.items():
            clauses.append(f"{table}.{k} >= %s")
            params.append(v)

    where_sql = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    order_sql = ""
    if order:
        order_parts = order.split(".")
        order_col = order_parts[0]
        order_dir = order_parts[1].upper() if len(order_parts) > 1 else "ASC"
        order_sql = f"ORDER BY {table}.{order_col} {order_dir}"

    limit_sql = f"LIMIT {int(limit)}" if limit else ""

    sql = f"SELECT {select_sql} FROM {table} {join_sql} {where_sql} {order_sql} {limit_sql}".strip()

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = [dict(r) for r in cur.fetchall()]
        if alias_map:
            rows = _renest(rows, alias_map)
        return rows
    finally:
        conn.close()


def update_rows(table, updates, filters):
    """
    Update rows matching `filters` with `updates`. Returns updated rows.
    """
    if not updates or not filters:
        return []

    set_cols = list(updates.keys())
    set_clause = ", ".join(f"{c} = %s" for c in set_cols)
    set_values = [_serialize_val(updates[c]) for c in set_cols]

    where_cols = list(filters.keys())
    where_clause = " AND ".join(f"{c} = %s" for c in where_cols)
    where_values = [filters[c] for c in where_cols]

    sql = f"UPDATE {table} SET {set_clause} WHERE {where_clause} RETURNING *"

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, set_values + where_values)
            updated = [dict(r) for r in cur.fetchall()]
        conn.commit()
        return updated
    finally:
        conn.close()


def upsert_rows(table, rows, on_conflict=None):
    """
    Insert or update rows on conflict.
    """
    if isinstance(rows, dict):
        rows = [rows]
    if not rows:
        return []

    conn = _connect()
    created = []
    try:
        with conn.cursor() as cur:
            for row in rows:
                cols = list(row.keys())
                placeholders = ["%s"] * len(cols)
                values = [_serialize_val(row[c]) for c in cols]
                conflict_clause = ""
                if on_conflict:
                    update_set = ", ".join(
                        f"{c} = EXCLUDED.{c}" for c in cols if c != on_conflict
                    )
                    conflict_clause = (
                        f"ON CONFLICT ({on_conflict}) DO UPDATE SET {update_set}"
                    )
                sql = (
                    f"INSERT INTO {table} ({', '.join(cols)}) "
                    f"VALUES ({', '.join(placeholders)}) {conflict_clause} RETURNING *"
                )
                cur.execute(sql, values)
                created.append(dict(cur.fetchone()))
        conn.commit()
    finally:
        conn.close()
    return created
