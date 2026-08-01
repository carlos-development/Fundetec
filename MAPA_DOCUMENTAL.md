# Mapa documental

## Fuentes vigentes

- `README.md`: estado general y acceso rapido.
- `financiacion_educativa/README.md`: dominio educativo y operacion tecnica.
- `docs/README.md`: indice por audiencia.
- `docs/api-aliados/GUIA_INTEGRACION_API.md`: guia para instituciones.
- `docs/api-aliados/openapi.yaml`: contrato mecanico de la API.
- `docs/interno/operaciones/REANUDAR_PROYECTO.md`: reanudacion local segura.
- `docs/interno/operaciones/MANTENIMIENTO_STAGING.md`: operacion segura de
  staging, correo y credenciales institucionales.

## Estado y trazabilidad

- `ESTADO_Y_BACKLOG.md`: deuda y trabajo pendiente.
- `HITOS_CERRADOS.md`: hitos historicos cerrados.
- `docs/interno/fases/`: auditorias y propuestas aun no implementadas.

## Documentacion heredada

`docs/archivo/legacy/` conserva material de libranza, marketplace y del
proyecto historico. Su presencia no significa que esas rutas o funciones esten
activas en el producto educativo.

Tambien existen documentos especializados heredados en la raiz. Deben
consultarse solo como referencia historica y nunca como contrato vigente.

## Regla de autoridad

Ante una contradiccion, prevalecen en este orden:

1. codigo, migraciones aplicables y pruebas;
2. esquema OpenAPI generado;
3. `financiacion_educativa/README.md`;
4. documentacion interna y archivo legado.
