# Cola persistente de automatizacion educativa

## Alcance

La automatizacion educativa usa una cola propia respaldada por PostgreSQL. El
proceso HTTP solo confirma la transaccion que deja el trabajo en `QUEUED`; no
ejecuta ClamAV, OpenAI, WeasyPrint ni ZapSign.

La automatizacion permanece deshabilitada por defecto. No debe activarse hasta
aplicar la migracion, configurar los backends, iniciar un worker independiente
y completar las pruebas de staging.

## Por que no se reutiliza Celery legacy

El proyecto registra Celery globalmente, pero las tareas existentes pertenecen
a `gestion_creditos` y su programacion historica. Financiacion educativa no
tenia una tarea, cola ni contrato de reintentos propio. Reutilizar ese worker
acoplaria el nuevo dominio al despliegue y a los fallos del producto historico.

La cola PostgreSQL permite, dentro del mismo dominio:

- confirmar solicitud y trabajo en la misma transaccion;
- reclamar filas con `select_for_update(skip_locked=True)`;
- recuperar leases vencidos despues de una caida;
- conservar etapa, intento y razon controlada sin almacenar PII ni secretos;
- reanudar una sola etapa sin repetir artefactos o envios ya confirmados.

No reemplaza un broker general. Si el volumen futuro lo exige, el puerto de
ejecucion puede migrarse a Celery conservando los modelos persistentes como
fuente de estado e idempotencia.

## Etapas

```text
SECURITY_SCAN
  -> DOCUMENT_VALIDATION
  -> DECISION
  -> FINANCIAL_SNAPSHOT
  -> CONTRACT_GENERATION
  -> SIGNATURE_SEND
  -> WAITING_SIGNATURE
  -> COMPLETED
```

Cada ejecucion procesa una etapa. Una firma valida del pagare vigente, recibida
por webhook autenticado e idempotente, es la unica que cambia el proceso de
`PENDING_SIGNATURE` a `COMPLETED` y la solicitud a `APPROVED`.

## Estados y recuperacion

- `QUEUED`: listo para reclamar.
- `RUNNING`: reclamado con lease temporal.
- `RETRYING`: error temporal con backoff exponencial acotado.
- `CORRECTION_REQUIRED`: el solicitante debe reemplazar evidencia.
- `MANUAL_EXCEPTION`: contingencia que no puede resolverse automaticamente.
- `PENDING_SIGNATURE`: espera exclusiva del webhook.
- `COMPLETED`: firma valida confirmada.
- `FAILED`: error permanente o agotamiento de intentos.

Los envios ambiguos a ZapSign terminan en `MANUAL_EXCEPTION`; no se reenvian
automaticamente. Los PDF que requieren inspeccion de contenido tampoco se
autoaceptan y terminan con `PDF_CONTENT_PROCESSING_REQUIRED`.

`CORRECTION_REQUIRED` exige una accion del solicitante. El reemplazo conserva
el documento anterior inactivo y, al volver a enviar el expediente, crea una
nueva version del proceso desde `SECURITY_SCAN`; las etapas de la version
anterior no se reutilizan.

`MANUAL_EXCEPTION` exige intervencion de un usuario con permisos de revision.
Para evidencia documental, el revisor aplica el procedimiento de contingencia,
acepta o solicita reemplazo y usa la decision operativa normal. Una aprobacion
manual valida crea una nueva version desde `CONTRACT_GENERATION`. Para un envio
ZapSign ambiguo, primero debe conciliarse el `external_id` con el proveedor; no
existe comando que reenvie automaticamente un proceso ambiguo.

`FAILED` es terminal para esa version. El diagnostico permite identificar el
conteo afectado, pero la recuperacion exige corregir la causa y aplicar el
procedimiento de contingencia; nunca se editan filas directamente.

WeasyPrint renderiza pagare y ficha fuera de la transaccion de persistencia.
La transaccion posterior bloquea la solicitud, vuelve a validar la fotografia
y conserva como maximo un artefacto vigente de cada tipo. ZapSign se invoca
despues de persistir `SENDING`; una caida inconclusa convierte ese intento en
`SIGNATURE_SEND_AMBIGUOUS` para conciliacion, no para reenvio ciego.

## Comandos

```bash
python manage.py diagnosticar_cola_educativa
python manage.py recuperar_cola_educativa
python manage.py procesar_cola_educativa --once
python manage.py procesar_cola_educativa --limit 20
```

El diagnostico solo presenta conteos agregados. No imprime solicitudes,
destinatarios, nombres, tokens, prompts ni respuestas de proveedores.

## Variables

```dotenv
FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=false
FINANCIACION_EDUCATIVA_WORKER_LEASE_SECONDS=180
FINANCIACION_EDUCATIVA_WORKER_MAX_ATTEMPTS=3
FINANCIACION_EDUCATIVA_WORKER_BACKOFF_BASE_SECONDS=15
FINANCIACION_EDUCATIVA_WORKER_BACKOFF_MAX_SECONDS=300
```

En staging y produccion, activar la automatizacion exige PostgreSQL. El check de
Django tambien valida que todos los parametros sean enteros positivos y que el
backoff base no supere el maximo.

## Servicio systemd de staging

La plantilla versionada y fuente unica es:

```text
deploy/systemd/fundetec-staging-educational-worker.service
```

Antes de activarla: migraciones en cero pendientes, `manage.py check`, backends
falsos o sandbox verificados, automatizacion aun en `false`, y prueba manual de
`--once`. El interruptor se activa al final y permite volver a contingencia sin
eliminar procesos ni historial.
