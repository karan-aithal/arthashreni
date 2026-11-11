"""Ledger: append-only transactions, lots (FIFO), corporate actions, reconciliation.

Pure logic lives in types / effective / lots / reconcile / fingerprint (no I/O);
repository / assets hold the SQL. Money is Decimal everywhere (ADR-0003).
"""
