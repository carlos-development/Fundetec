# Outbox de correos educativos

## Alcance y garantia

Los correos funcionales de financiacion educativa se originan mediante
`OutboxCorreoEducativo`. La intencion se crea en la misma transaccion que el
evento de dominio y el request HTTP no contacta SMTP ni registra callbacks de
entrega con `transaction.on_commit`.

El outbox cubre:

- invitacion inicial y reemisiones;
- enlace personal de captura movil;
- confirmacion de expediente recibido;
- decisiones de revision manual;
- correccion documental automatica.
- continuacion a preparacion contractual despues de una validacion automatica
  concluyente.

`enviar_correos_prueba_educacion` queda fuera del flujo: es una herramienta
operativa explicita que genera muestras inertes sin solicitud ni evento de
dominio. No debe ejecutarse como worker ni para notificar usuarios.

La garantia real es **al menos una intencion persistente y recuperable**. La
clave idempotente impide duplicados internos y el `Message-ID` determinista
ayuda a conciliar, pero SMTP no ofrece entrega exactamente una vez ni obliga a
los servidores receptores a deduplicar.

## Estados

- `PENDING`: listo para reclamar.
- `SENDING`: reclamado por un worker con `lease_id` y vencimiento.
- `RETRYING`: fallo anterior a una aceptacion conocida; espera backoff.
- `SENT`: entrega confirmada por el backend.
- `FAILED`: configuracion, autenticacion o rechazo permanente.
- `AMBIGUOUS`: pudo transmitirse antes de un timeout, desconexion o caida.

`AMBIGUOUS` no se reenvia automaticamente. Requiere conciliacion operativa y
una resolucion explicita. Ningun estado del correo modifica estados
financieros, contractuales, autorizacion del curso ni firma.

## Reclamo y timeouts

El worker reclama una fila con `select_for_update(skip_locked=True)` dentro de
una transaccion breve, guarda el lease y confirma la transaccion. SMTP se
contacta despues, sin mantener bloqueos de PostgreSQL. Solo el propietario del
lease puede finalizar el intento.

El lease debe superar `EMAIL_TIMEOUT`; `manage.py check` valida esa relacion.
Con los valores iniciales, SMTP tiene 10 segundos y el lease 120 segundos. Los
clientes externos del dominio tambien tienen timeout explicito: ClamAV separa
conexion y lectura, OpenAI usa el timeout educativo y ZapSign usa el timeout de
firma para POST, consultas y descarga.

## Enlaces personales

El outbox no almacena token ni URL. Al reclamar una invitacion o captura, el
worker emite un token nuevo dentro de una operacion atomica y conserva la URL
solo en memoria durante el envio. Un reintento recuperado rota el token; los
anteriores quedan revocados. Entregas reemplazadas o consumidas fallan de forma
cerrada y nunca se reactivan.

Invitaciones y captura solo se envian al solicitante, sin CC. La confirmacion
de expediente conserva el CC operativo configurado.

## Comandos

```bash
python manage.py procesar_outbox_educativo --once
python manage.py procesar_outbox_educativo --limit 20
python manage.py diagnosticar_outbox_educativo
python manage.py diagnosticar_outbox_educativo --solicitud-id UUID
python manage.py recuperar_outbox_educativo --recover-leases --dry-run
python manage.py recuperar_outbox_educativo --recover-leases --confirmar
python manage.py recuperar_outbox_educativo --retry-failed --outbox-id UUID --dry-run
python manage.py recuperar_outbox_educativo --retry-failed --outbox-id UUID --confirmar
python manage.py recuperar_outbox_educativo --resolve-ambiguous SENT --outbox-id UUID --dry-run
python manage.py recuperar_outbox_educativo --resolve-ambiguous SENT --outbox-id UUID --confirmar
```

El diagnostico imprime solo conteos. Recuperar leases vencidos los clasifica
como `AMBIGUOUS`; no asume que SMTP no recibio el mensaje. Reintentar `FAILED`
y resolver `AMBIGUOUS` exigen `--confirmar`, salvo en `--dry-run`. No se deben
editar estados mediante SQL.

## Variables

```dotenv
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_LEASE_SECONDS=120
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_MAX_ATTEMPTS=3
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_BASE_SECONDS=30
FINANCIACION_EDUCATIVA_EMAIL_OUTBOX_BACKOFF_MAX_SECONDS=600
```

`EMAIL_QA_MODE`, `EMAIL_LIVE_DELIVERY_ENABLED`, `EMAIL_TIMEOUT` y la validacion
de `SafeRoutingEmailBackend` siguen aplicando. El outbox no habilita entregas
reales ni relaja la configuracion por ambiente.

## Servicio systemd de staging

La plantilla versionada y fuente unica es:

```text
deploy/systemd/fundetec-staging-email-outbox.service
```

Antes de iniciarla se ejecuta `diagnosticar_outbox_educativo`. El comando
muestra conteos agregados de `PENDING`, `RETRYING`, `FAILED`, `AMBIGUOUS` y los
demas estados sin imprimir destinatarios ni datos de solicitudes. Cualquier
`AMBIGUOUS` debe conciliarse; nunca se reenvia automaticamente.

## Riesgos fuera de este bloque

- Un `SIGKILL` despues de escribir un archivo privado y antes de persistir su
  referencia puede dejar un archivo huerfano. Se requiere una conciliacion
  futura por antiguedad y referencias; no debe eliminarse por nombre ni durante
  una transaccion activa.
- Las pruebas JavaScript de camara caracterizan flujo y sintaxis. Falta validar
  captura real, permisos, orientacion y cambio de camara en Android/iOS.
- La concurrencia `skip_locked` se prueba solamente con PostgreSQL. SQLite no
  reproduce esa garantia y la prueba queda marcada como omitida.
