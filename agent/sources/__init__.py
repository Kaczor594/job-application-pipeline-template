"""Active-sourcing connectors.

Each source module exposes `search(query: JobQuery) -> list[JDExtraction]`, returning
the SAME JDExtraction shape the email path uses, so discovered jobs flow through the
identical dedup → rank → pipeline machinery. Sources are documented APIs/feeds, never
HTML scraping.
"""
