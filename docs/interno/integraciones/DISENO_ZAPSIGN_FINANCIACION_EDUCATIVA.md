# Diseno de adaptacion ZapSign para financiacion educativa

## Estado comprobado

La integracion existente pertenece a `gestion_creditos` y no esta conectada al
dominio educativo. Sus puntos son `gestion_creditos.models.Pagare`,
`ZapSignWebhookLog`, `services.pagare_service.generar_pagare_pdf`,
`services.zapsign_client.ZapSignClient` y
`views.integrations.zapsign_webhook_view`. El webhook historico no esta
expuesto tras el saneamiento 6.5. No se activo, no se invoco la API y no se
usaron credenciales en esta fase.

## Reutilizacion con aislamiento

Pueden extraerse el cliente HTTP con timeouts, el hash del PDF, la descarga del
firmado y el mapeo defensivo de estados. No debe importarse el servicio
historico: depende de `Credito`, libranza, tasas, desembolso y plantillas
heredadas. Educacion requiere modelos, plantilla y servicios propios basados en
la fotografia financiera bloqueada y el expediente aprobado.

## Secuencia objetivo

1. Expediente completo y revision administrativa.
2. Correccion o rechazo si no cumple.
3. Aprobacion administrativa explicita.
4. Bloqueo contractual y generacion versionada del pagare.
5. Creacion idempotente en ZapSign.
6. Identificacion y firma.
7. Webhook autenticado e idempotente.
8. Descarga, hash y almacenamiento privado del PDF firmado.
9. Estado de pagare valido; la matricula se registra por separado.

**Enviar a revision no envia un pagare.** El envio debe ocurrir solo despues de
la aprobacion administrativa.

## Configuracion y firmantes

La implementacion historica reconoce `ZAPSIGN_API_TOKEN`,
`ZAPSIGN_WEBHOOK_SECRET`, `ZAPSIGN_WEBHOOK_HEADER`, `ZAPSIGN_ENVIRONMENT`,
`ZAPSIGN_AUTH_MODE`, `ZAPSIGN_SEND_AUTOMATIC_EMAIL`,
`ZAPSIGN_ENABLE_SELFIE_VALIDATION` y
`ZAPSIGN_SELFIE_VALIDATION_TYPE`. Los secretos deben quedar fuera de
logs/admin y sandbox debe separarse de produccion.

Adulto: estudiante como deudor principal. Menor: tutor/deudor principal y, solo
si juridica lo exige, estudiante posterior. Numero, orden, tipo de firma,
autenticacion y evidencia biometrica requieren aprobacion juridica y del plan
ZapSign contratado.

## Brechas de seguridad e idempotencia

El webhook historico considera valida la peticion cuando el secreto esta vacio,
almacena payload y headers completos, y descarga el PDF dentro del recorrido
sin una cola recuperable. No es apto para exposicion educativa.

El componente nuevo debe exigir secreto o firma oficial, comparar en tiempo
constante, deduplicar por identificador externo, validar transiciones con
bloqueo, filtrar headers, descargar fuera de la transaccion y reintentar con
backoff. Debe manejar `PENDING`, `SENT`, `SIGNED`, `REFUSED`, `FAILED` y
`CANCELLED`. En local usara un adaptador falso sin red y nunca marcara una firma
como real.

## Decisiones pendientes

- texto contractual, acreedor y plantilla versionada;
- reglas de representacion del menor y orden de firmantes;
- mecanismo oficial de autenticidad del webhook;
- capacidades y limites del contrato ZapSign;
- retencion y acceso al firmado;
- significado operativo posterior a firma, sin asumir desembolso.
