"""Isolated Image Flow domain. No video queue dependencies.

Phase P0: pure transition policy, not a live scheduler or HTTP API.
Persist mutations in a tenant-scoped locked PostgreSQL transaction before
connecting real workers. Production claims remain disabled.
"""
