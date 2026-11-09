"""Optional Firecrawl Markdown export for an explicitly configured URL allow-list.

This helper is invoked as a script from the repository root. Add that root to
sys.path before importing the project package, because Python otherwise starts
with tools/ as the first import location.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    urls = [
        value.strip()
        for value in os.getenv("FIRECRAWL_RAG_URLS", "").splitlines()
        if value.strip()
    ]
    api_key = os.getenv("FIRECRAWL_API_KEY", "").strip()

    # Firecrawl is optional: do not import its adapter or fail the monitor when
    # the user has not configured both required inputs.
    if not urls or not api_key:
        print("Firecrawl RAG omitido: configura FIRECRAWL_API_KEY y FIRECRAWL_RAG_URLS.")
        return 0

    try:
        from scrapers.firecrawl_enrichment import export_rag_context
    except ImportError as exc:
        print(f"::warning::Firecrawl RAG omitido: no se pudo cargar el módulo ({type(exc).__name__}).")
        return 0

    count = export_rag_context(urls)
    print(f"Firecrawl RAG: {count} documentos Markdown preparados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
