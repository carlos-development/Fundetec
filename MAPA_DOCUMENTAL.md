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
- `docs/interno/operaciones/GUIA_REVISION_MANUAL_FINANCIACION_EDUCATIVA.md`:
  procedimiento de contingencia manual; no describe el recorrido automatico
  definitivo.
- `docs/interno/operaciones/COLA_AUTOMATIZACION_EDUCATIVA.md`: estados,
  reintentos y operacion de la cola educativa persistente.
- `docs/interno/operaciones/OUTBOX_CORREOS_EDUCATIVOS.md`: atomicidad,
  entrega, conciliacion y operacion de los correos educativos persistentes.

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
