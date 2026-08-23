"""Service layer: orchestration/enrichment logic extracted from main.py
(architecture review P3-5.1, first batch).

Keeps main.py as pure route assembly; stateful domain services live here.
Current members: enrichment (replay list enrichment with in-process cache).
"""
