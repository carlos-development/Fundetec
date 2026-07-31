# Fase 7 - Panel operativo de financiacion educativa

## Objetivo y alcance

Construir una aplicacion operativa propia para revisar expedientes. Django Admin
queda como herramienta tecnica. Incluye bandeja, busqueda, filtros, detalle de
estudiante/tutor, trazabilidad, visor privado, revision documental, solicitud
de correcciones, aprobacion/rechazo, auditoria y permisos por objeto.

No incluye pagare, ZapSign, pagos, recaudos, mora, desembolso, OCR, IA ni
scoring. Cada integracion tendra una fase propia.

## Roles

| Rol | Alcance | Acciones |
| --- | --- | --- |
| Operador documental | Asignadas | Revisar documentos y pedir correccion |
| Analista | Instituciones permitidas | Cerrar revision, aprobar o rechazar |
| Supervisor | Alcance supervisado | Reasignar y reabrir con motivo |
| Auditor | Lectura completa | Consultar/exportar trazabilidad |
| Administrador de seguridad | Usuarios y roles | Administrar acceso, no aprobar |

Se aplicara minimo privilegio, separacion entre aprobacion y accesos,
autorizacion por objeto y bitacora con actor, fecha, IP, accion, estados y
motivo. Las vistas no confiaran en IDs enviados por el cliente.

## Estados y acciones

El primer incremento opera `PENDING_MANUAL_REVIEW`. Una correccion vuelve a una
etapa documental mediante servicio de dominio y una aprobacion pasa a
`PENDING_PROMISSORY_NOTE`. Rechazo, cancelacion y reapertura requieren reglas
aprobadas antes de programarse.

## Indicadores

Disponibles hoy: solicitudes recibidas, incompletas, en revision, conversion
por etapa, distribucion por institucion/curso/periodo, monto solicitado, capital
financiado y tiempo por estado. Aprobadas/rechazadas se habilitan cuando existan
transiciones operativas confiables.

Pendientes de firma: pagares generados, enviados, firmados, rechazados y
vencidos. Pendientes de pagos: cartera, capital pendiente, recaudos, capital e
intereses recuperados, mora, vencida y pagos anticipados.

`Colocacion aprobada` representa obligaciones aprobadas para financiar el plan
educativo; no implica dinero desembolsado al estudiante. `Captaciones` no se
implementa porque puede aludir a recursos del publico y requiere definicion de
negocio, juridica y cumplimiento.

## Incrementos

1. Permisos, bandeja y detalle solo lectura.
2. Revision documental y correcciones.
3. Aprobacion/rechazo con maquina de estados y auditoria.
4. Indicadores disponibles, SLA y exportacion controlada.
5. Integracion posterior con pagare mediante puertos propios.

Cada bloque exige pruebas de rol, IDOR, CSRF, transiciones, concurrencia,
auditoria, filtros, paginacion y consultas.
