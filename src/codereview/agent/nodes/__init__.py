"""Graph nodes.

Deterministic nodes (ingest, static_scan, aggregate) do the cheap, reliable work
before any token is spent. LLM nodes (plan, review, verify) only run where
judgement is genuinely required.
"""

from .aggregate import aggregate_node
from .ingest import ingest_node, route_after_ingest, static_scan_node
from .plan import fanout, plan_node
from .report import report_node
from .review import review_file_node
from .verify import verify_node

__all__ = [
    "aggregate_node",
    "fanout",
    "ingest_node",
    "plan_node",
    "report_node",
    "review_file_node",
    "route_after_ingest",
    "static_scan_node",
    "verify_node",
]
