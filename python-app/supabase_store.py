#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Supabase compatibility layer.
Redirects all database store operations to db_store.py (Neon / Lakebase Postgres).
"""

from db_store import (  # noqa: F401
    DATABASE_URL,
    configured,
    insert_rows,
    select_rows,
    update_rows,
    upsert_rows,
)
