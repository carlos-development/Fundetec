# Documentacion

Este directorio separa la documentacion vigente por audiencia y conserva el
material heredado sin presentarlo como parte del producto educativo actual.

## Integracion de aliados

- `api-aliados/GUIA_INTEGRACION_API.md`: contrato funcional y operativo.
- `api-aliados/openapi.yaml`: contrato OpenAPI generado desde Django.
- `api-aliados/postman/aprobado-financiacion-educativa.postman_collection.json`:
  coleccion reproducible.

## Uso interno

- `interno/arquitectura/`: fuente HTML y documento tecnico derivado.
- `interno/operaciones/`: procedimientos para reanudar y validar el proyecto.
- `interno/operaciones/MANTENIMIENTO_STAGING.md`: configuracion, correo y
  credenciales institucionales del staging desplegado.
- `interno/operaciones/GUIA_REVISION_MANUAL_FINANCIACION_EDUCATIVA.md`:
  contingencia manual cuando la automatizacion esta deshabilitada, pausada o
  termina en una excepcion operativa.
- `interno/operaciones/COLA_AUTOMATIZACION_EDUCATIVA.md`: arquitectura,
  recuperacion y plantilla del worker educativo persistente.
- `interno/operaciones/OUTBOX_CORREOS_EDUCATIVOS.md`: garantia real,
  recuperacion y comandos del outbox de correo educativo.
- `interno/fases/`: auditorias y disenos de fases pendientes.
- `interno/integraciones/`: disenos no habilitados de integraciones futuras.

Los documentos internos no deben entregarse a instituciones aliadas.

## Archivo legado

`archivo/legacy/` conserva decisiones y procedimientos de dominios heredados.
No describe rutas publicas ni capacidades vigentes de financiacion educativa.

## Regla de vigencia

El codigo y sus pruebas son la autoridad funcional. El esquema OpenAPI debe
regenerarse y validarse cuando cambie el contrato institucional. El documento
Word de arquitectura se deriva de
`interno/arquitectura/documentacion_tecnica_fuente.html`.
