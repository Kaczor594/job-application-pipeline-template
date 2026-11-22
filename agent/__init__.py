"""Nightly job-application agent.

Wraps the unchanged apply.py pipeline with email ingestion, JD extraction,
relevance ranking, a morning digest, and application-status tracking.

See CLAUDE_HANDOFF.md / the plan for architecture. All tunables live in config.py.
"""
