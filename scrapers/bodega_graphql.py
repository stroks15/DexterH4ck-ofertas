"""Parser GraphQL de Bodega Aurrera basado en el parser Walmart."""

from scrapers.walmart_graphql import WalmartGraphQLParser


class BodegaGraphQLParser(WalmartGraphQLParser):
    def __init__(self, marcas_prioritarias=None):
        super().__init__(
            marcas_prioritarias=marcas_prioritarias,
            tienda="Bodega Aurrera",
            dominio="https://www.bodegaaurrera.com.mx",
        )
