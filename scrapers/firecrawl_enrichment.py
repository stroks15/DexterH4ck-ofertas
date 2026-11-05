"""Optional Firecrawl enrichment for RAG/AI context (never the price authority).

Firecrawl's rendered Markdown is stored as ephemeral JSONL context, not as product
prices. Deal eligibility must always come from a trusted store API or price history.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import requests

API_BASE = os.getenv("FIRECRAWL_API_URL", "https://api.firecrawl.dev/v2").rstrip("/")
TIMEOUT = max(5, int(os.getenv("FIRECRAWL_TIMEOUT", "45")))


def scrape_markdown(url: str) -> dict | None:
    key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    if not key:
        return None
    response = requests.post(
        f"{API_BASE}/scrape",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        json={"url": url, "formats": ["markdown"], "onlyMainContent": True},
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    data = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(data, dict):
        return None
    return {"url": url, "title": data.get("metadata", {}).get("title", ""),
            "markdown": data.get("markdown", ""), "metadata": data.get("metadata", {})}


def export_rag_context(urls: Iterable[str], output: str = ".cache/firecrawl/rag_context.jsonl") -> int:
    """Fetch a small, explicit URL set and export Markdown records for later RAG use."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with destination.open("w", encoding="utf-8") as stream:
        for url in urls:
            try:
                record = scrape_markdown(url)
                if record and record.get("markdown"):
                    stream.write(json.dumps(record, ensure_ascii=False) + "\n")
                    count += 1
            except (requests.RequestException, ValueError, TypeError) as exc:
                # Fail open: optional enrichment never stops the deal monitor.
                print(f"Firecrawl omitió {url}: {type(exc).__name__}")
    return count
