"""Consulta GraphQL compartida para catálogos Walmart/Bodega.

El endpoint real se configura mediante WALMART_GRAPHQL_URL/BODEGA_GRAPHQL_URL.
No se hardcodea un endpoint interno no documentado.
"""

WALMART_GRAPHQL_QUERY = """
query SearchAndFilter($searchQuery: String!, $facetFilters: String, $page: Int, $size: Int, $storeId: String!) {
  search(query: $searchQuery, facetFilters: $facetFilters, page: $page, size: $size, storeId: $storeId) {
    products {
      id
      name
      brand
      canonicalUrl
      priceInfo {
        currentPrice { price }
        wasPrice { price }
      }
    }
  }
}
"""
