"""Optional Firecrawl Markdown export for an explicitly configured URL allow-list."""
import os
from scrapers.firecrawl_enrichment import export_rag_context


def main() -> int:
    urls = [value.strip() for value in os.getenv("FIRECRAWL_RAG_URLS", "").splitlines() if value.strip()]
    if not urls or not os.getenv("FIRECRAWL_API_KEY"):
        print("Firecrawl RAG omitido: configura FIRECRAWL_API_KEY y FIRECRAWL_RAG_URLS.")
        return 0
    count = export_rag_context(urls)
    print(f"Firecrawl RAG: {count} documentos Markdown preparados.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
