# Integración

`api_first_scrapers()` conserva todos sus adaptadores actuales. Con `SCRAPLING_MOTOR_ENABLED=true`, los adaptadores del motor aparecen antes de la lista existente; cada uno llama perezosamente al adaptador legacy equivalente si el motor devuelve cero filas, bloqueo, timeout o no está disponible.

Sin la variable de entorno (valor por defecto), no se añaden adaptadores de motor y el comportamiento API-first existente no cambia. Esto evita red adicional durante tests y permite instalar las dependencias opcionales gradualmente.

La salida de cada adaptador sigue el esquema de candidatos vigente y no publica. El dedupe y publicación siguen siendo responsabilidad de `monitor_ofertas`.
