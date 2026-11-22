# Correcciones asociadas

- Se unificó la obtención del prefijo de `VtexCatalogAdapter` con `removesuffix("_VTEX_ENDPOINT").removesuffix("_ENDPOINT")`, para que consultas y páginas usen el mismo prefijo.
- Se añadieron aliases de identidad para las tiendas del perímetro, dejando `sams club` antes de `sams`.
- Se ampliaron patrones de ID de producto para URLs públicas genéricas nuevas sin cambiar la identidad de proveedores existentes.
- `TIENDAS_OBJETIVO` añade nueve nombres de forma idempotente mediante `tuple(dict.fromkeys(...))`.
- `tools/verificar_motor.py` se puede ejecutar desde la raíz con `python tools/verificar_motor.py`.
