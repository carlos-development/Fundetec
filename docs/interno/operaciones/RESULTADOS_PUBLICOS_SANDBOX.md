# Certificacion de resultados publicos Sandbox

Esta herramienta simula respuestas de API. **Jamas representa una aprobacion
real, firma valida ni autorizacion contractual del curso.** No usar el ambiente
de certificacion como integracion productiva. No crea contratos, decisiones,
correos ni eventos ficticios. No modifica el estado interno de la solicitud.

Requiere migracion `0027_resultado_publico_sandbox`, ambiente declarado
`DEPLOYMENT_ENVIRONMENT=staging` o `test` y un usuario activo, staff, con permiso
`financiacion_educativa.change_resultadopublicosandboxsolicitud` o superusuario.
`--actor-id` identifica al administrador responsable; el acceso al shell debe
restringirse a operadores autorizados. No es un mecanismo de autenticacion remota.
El Admin permite consultar las filas, no crearlas, editarlas ni borrarlas.

En production (tambien en local u otro ambiente), una fila accidental, incluso
inactiva, bloquea explicitamente la construccion del resultado publico. No se
ignora silenciosamente ni permite aparentar una aprobacion productiva.

## Ejecucion posterior al despliegue autorizado

No ejecutados durante la implementacion. Reemplazar `ID_ADMIN` por el ID numerico
del administrador autorizado. Ejecutar con el entorno y Python del servicio.

```sh
staging_manage simular_resultado_api_sandbox --application-id e0fc16c7-4710-4714-8b27-cbde6b1e8f7e --actor-id ID_ADMIN --status APPROVED --confirm
staging_manage simular_resultado_api_sandbox --application-id dcb28009-1dbb-43c9-9e32-af429657222c --actor-id ID_ADMIN --status REJECTED --decision-reason OTHER --confirm
```

No simular `f1d636c0-64fb-412a-96e2-cb67fcea8bab`: su GET sigue mostrando el
resultado real. RECEIVED requiere que su estado interno corresponda a ese
resultado; este comando no lo fuerza ni cambia estados para conseguirlo.

GET institucional mantiene autenticacion y aislamiento por institucion. El
snapshot APPROVED se calcula una vez con el motor y configuracion vigentes.
Una repeticion identica conserva snapshot y fecha, aunque cambie la politica
financiera. POST y sus replays no aplican la simulacion ni reenvian invitaciones.
`authorization_effective_at` es la fecha de simulacion, no de una firma real.
`updated_at` refleja la ultima modificacion real o de simulacion.

## Retirar una simulacion

```sh
staging_manage simular_resultado_api_sandbox --application-id UUID --actor-id ID_ADMIN --clear --confirm
```

Desactiva la fila y conserva creador, ultimo responsable, fechas y snapshot para
auditoria. No borra la solicitud. Repetir clear no cambia fechas; clear sin fila
no crea registros. GET vuelve al resultado real y refleja la fecha de retirada.
Reactivar o cambiar el resultado recalcula el snapshot cuando sea APPROVED.
No elimina ni reinterpreta historial contractual.

La limpieza masiva QA reconoce estas filas pero no las elimina: su relacion
PROTECT bloquea y revierte la limpieza de solicitudes con auditoria Sandbox,
incluso tras clear. Las solicitudes sin simulacion conservan la limpieza previa.

Verificar los tres UUID mediante GET autenticado desde la institucion propietaria.
No imprimir credenciales ni datos personales en diagnosticos compartidos.
