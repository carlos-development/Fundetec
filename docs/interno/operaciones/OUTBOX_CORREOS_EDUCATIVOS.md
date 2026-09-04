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
- recordatorios de continuacion ya vencidos;
- notificacion interna de una solicitud nueva, cuando existen destinatarios
  operativos configurados.
- copias independientes de auditoria e institucionales creadas solo despues
  de confirmar el mensaje estudiantil original como `SENT`.

`enviar_correos_prueba_educacion` queda fuera del flujo: es una herramienta
operativa explicita que genera muestras inertes sin solicitud ni evento de
dominio. No debe ejecutarse como worker ni para notificar usuarios.

La garantia real es **al menos una intencion persistente y recuperable**. La
clave idempotente impide duplicados internos y el `Message-ID` determinista
ayuda a conciliar, pero SMTP no ofrece entrega exactamente una vez ni obliga a
los servidores receptores a deduplicar.

## Copias posteriores a SENT

Las copias no usan `CC` ni `BCC`. Cada una es una fila independiente con
`Message-ID`, lease, reintentos y clave idempotente propios, y conserva una FK
`correo_origen` al mensaje estudiantil confirmado. Una restriccion impide mas
de una copia de cada clase por original. El fallo o reintento de una copia no
modifica ni reenvia el original y no cambia el estado de la solicitud.

La allowlist cerrada de eventos auditables es:

- `INITIAL_INVITATION`;
- `INVITATION_REISSUE`;
- `CONTINUATION_REMINDER_1H`;
- `CONTINUATION_REMINDER_6H`;
- `CONTINUATION_REMINDER_24H`;
- `CONTINUATION_REMINDER_48H`;
- `MOBILE_CAPTURE_LINK`;
- `DOSSIER_RECEIVED`;
- `REVIEW_DECISION`;
- `AUTOMATIC_CORRECTION`;
- `AUTOMATIC_CONTINUATION`.

`NEW_APPLICATION_INTERNAL` no pertenece a la allowlist y conserva su
configuracion y comportamiento. Actualmente no existe otro evento estudiantil
independiente para finalizacion; una decision final comunicada usa
`REVIEW_DECISION`.

Las copias usan los codigos explicitos `AUDIT_COPY` e
`INSTITUTIONAL_INITIAL_NOTIFICATION`. Una copia nunca genera otra copia. El
despliegue no recorre correos `SENT` historicos: solo una nueva confirmacion
`SENT` crea las intenciones secundarias.

El contexto persistido de una copia es siempre `{}` y no admite destinatarios
en copia. El texto y HTML se reconstruyen desde metadatos minimizados: clase de
comunicacion, asunto conocido, referencia, institucion, programa, curso,
destinatario original enmascarado, fecha de envio y estado. Nunca se copia el
cuerpo original, tokens, URLs personales, documentos ni credenciales. Los
enlaces se sustituyen por `Enlace personal omitido en esta copia por
seguridad.`

| Comunicacion | Estudiante | APROBADO | FUNDETEC |
| --- | ---: | ---: | ---: |
| Invitacion inicial | Si | Si | Si |
| Reemision de invitacion | Si | Si | No |
| Captura movil | Si | Si | No |
| Recordatorios | Si | Si | No |
| Correccion | Si | Si | No |
| Expediente/firma | Si | Si | No |
| Decision/finalizacion | Si | Si | No |
| Mensajes internos | No aplica | Segun configuracion existente | No |

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
python manage.py programar_recordatorios_solicitudes_educativas --dry-run
python manage.py programar_recordatorios_solicitudes_educativas --batch-size 100
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
FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_1_HOURS=1
FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_2_HOURS=6
FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_3_HOURS=24
FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_FINAL_HOURS=48
FINANCIACION_EDUCATIVA_CONTINUATION_MAX_MESSAGES=4
FINANCIACION_EDUCATIVA_CONTINUATION_REMINDER_BATCH_SIZE=100
EDUCATIONAL_OPERATIONS_NOTIFICATION_EMAILS=
EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=
```

La invitacion inicial cuenta dentro de `CONTINUATION_MAX_MESSAGES`. El valor
seguro `4` permite recordatorios a 1 y 6 horas, seguido del ultimo recordatorio
automatico a las 24 horas. El evento de 48 horas permanece disponible, pero
solo queda habilitado si se aprueba y configura un maximo de `5`. La lista
operativa vacia omite la notificacion interna y registra un codigo no sensible
en logs, sin impedir la creacion de la solicitud.

`EMAIL_QA_MODE`, `EMAIL_LIVE_DELIVERY_ENABLED`, `EMAIL_TIMEOUT` y la validacion
de `SafeRoutingEmailBackend` siguen aplicando. El outbox no habilita entregas
reales ni relaja la configuracion por ambiente.

`EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS` acepta una lista separada por comas.
Se normaliza a minusculas, elimina duplicados y rechaza direcciones invalidas
al cargar settings. Un valor vacio omite la auditoria con el codigo estable
`NO_AUDIT_RECIPIENTS`, sin afectar al estudiante.

Los destinatarios de FUNDETEC no se guardan en variables ni se deducen de
membresias. En Admin, abrir **Financiacion educativa > Destinatarios
institucionales educativos** y crear uno o mas registros activos para la
unica fila `Institucion` que representa a FUNDETEC. Las solicitudes de PREICFES,
INGLES y los demas programas deben referenciar esa misma institucion; el
programa o curso se conserva en los campos academicos de la solicitud. La
configuracion institucional se registra una sola vez en FUNDETEC. No se deben
crear filas `Institucion` por programa ni duplicar sus destinatarios. Las
direcciones inactivas se omiten y otra institucion conserva configuracion
independiente.

La ausencia de destinatarios activos registra
`NO_INSTITUTION_RECIPIENTS`. Los fallos de creacion usan
`AUDIT_COPY_CREATE_FAILED` o `INSTITUTION_COPY_CREATE_FAILED`; los logs solo
incluyen el ID tecnico del outbox, el codigo y la clase de error.

## Despliegue de la migracion 0026

1. Mantener detenido el worker de outbox y respaldar PostgreSQL.
2. Desplegar el codigo y agregar `EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS=` al
   entorno, inicialmente vacio.
3. Revisar `python manage.py migrate --plan` y aplicar
   `python manage.py migrate financiacion_educativa 0026`.
4. Ejecutar `python manage.py check` y reiniciar solo la aplicacion educativa.
5. Confirmar que PREICFES, INGLES y los demas programas originan solicitudes
   asociadas a la unica `Institucion` FUNDETEC, y registrar una sola vez en esa
   institucion sus destinatarios oficiales activos.
6. Configurar las direcciones de auditoria aprobadas, reiniciar la aplicacion y
   el worker para recargar el entorno, y validar primero con destinatarios de
   prueba autorizados.
7. Ejecutar `diagnosticar_outbox_educativo` antes de reanudar el worker. No
   conciliar ni reenviar filas `AMBIGUOUS` sin verificacion operativa.

La migracion no crea destinatarios, no envia mensajes y no reprocesa historia.
Para rollback operativo, vaciar `EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS`,
desactivar los destinatarios institucionales y reiniciar aplicacion/worker. Se
puede volver al codigo anterior dejando `0026` aplicada; sus columnas son
aditivas. Solo antes de generar copias, y tras respaldo y verificacion de cero
filas secundarias, puede revertirse el esquema con
`python manage.py migrate financiacion_educativa 0025`. No borrar filas del
outbox manualmente.

## Servicio systemd de staging

La plantilla versionada y fuente unica es:

```text
deploy/systemd/fundetec-staging-email-outbox.service
```

Antes de iniciarla se ejecuta `diagnosticar_outbox_educativo`. El comando
muestra conteos agregados de `PENDING`, `RETRYING`, `FAILED`, `AMBIGUOUS` y los
demas estados sin imprimir destinatarios ni datos de solicitudes. Cualquier
`AMBIGUOUS` debe conciliarse; nunca se reenvia automaticamente.

### Propuesta de ejecucion horaria

El worker de outbox permanece continuo. El programador de recordatorios debe
ejecutarse una vez por hora mediante un `systemd timer` o cron administrado,
con el usuario de servicio y el mismo `EnvironmentFile` de staging:

```bash
/var/www/fundetec-staging/shared/venv/bin/python \
  /var/www/fundetec-staging/current/manage.py \
  programar_recordatorios_solicitudes_educativas --batch-size 100
```

El comando solo crea intenciones persistentes; no contacta SMTP. Esta propuesta
no autoriza crear ni activar unidades en servidores.

## Recuperacion controlada de una invitacion existente

La recuperacion no crea ni elimina solicitudes y no modifica referencias o
estados manualmente. Antes de operar, reemplazar solamente el valor sintetico
de `REFERENCIA` y ejecutar el diagnostico con el entorno de staging ya cargado:

```python
from django.utils import timezone

from financiacion_educativa.choices import (
    CodigoMensajeCorreoEducativo,
    EstadoInvitacionContinuacion,
    TipoEventoCorreoEducativo,
)
from financiacion_educativa.models import SolicitudFinanciacionEducativa

REFERENCIA = 'REEMPLAZAR_REFERENCIA_EXTERNA'
solicitudes = SolicitudFinanciacionEducativa.objects.filter(
    referencia_externa=REFERENCIA,
)
print('APPLICATION_COUNT=', solicitudes.count())
if solicitudes.count() != 1:
    raise RuntimeError('La referencia no identifica exactamente una solicitud.')

solicitud = solicitudes.get()
local, dominio = solicitud.correo.rsplit('@', 1)
correo_enmascarado = f'{local[:1]}***@{dominio}'
print('APPLICATION_ID=', solicitud.pk)
print('STATE=', solicitud.estado)
print('EMAIL_MASKED=', correo_enmascarado)
print('USER_LINKED=', bool(solicitud.usuario_id))
print('AUTOMATION_PROCESS_COUNT=', solicitud.procesos_automatizacion.count())

for invitacion in solicitud.invitaciones_continuacion.order_by('creada_en'):
    print(
        'INVITATION=', invitacion.pk,
        'STATE=', invitacion.estado,
        'EXPIRED=', invitacion.vence_en <= timezone.now(),
    )
for entrega in solicitud.entregas_invitacion.order_by('secuencia'):
    print(
        'DELIVERY=', entrega.pk,
        'SEQUENCE=', entrega.secuencia,
        'ORIGIN=', entrega.origen,
        'STATE=', entrega.estado,
        'REPLACES=', entrega.reemplaza_a_id,
    )
for correo in solicitud.correos_outbox.order_by('creada_en'):
    print(
        'OUTBOX=', correo.pk,
        'EVENT=', correo.tipo_evento,
        'MESSAGE=', correo.codigo_mensaje,
        'STATE=', correo.estado,
        'ORIGIN_OUTBOX=', correo.correo_origen_id,
    )

originales = solicitud.correos_outbox.filter(correo_origen__isnull=True)
print('INITIAL_INVITATION_COUNT=', originales.filter(
    tipo_evento=TipoEventoCorreoEducativo.INITIAL_INVITATION,
).count())
print('INVITATION_REISSUE_COUNT=', originales.filter(
    tipo_evento=TipoEventoCorreoEducativo.INVITATION_REISSUE,
).count())
print('INSTITUTIONAL_INITIAL_NOTIFICATION_COUNT=',
      solicitud.correos_outbox.filter(
          codigo_mensaje=(
              CodigoMensajeCorreoEducativo.INSTITUTIONAL_INITIAL_NOTIFICATION
          ),
      ).count())
print('AUDIT_COPY_COUNT=', solicitud.correos_outbox.filter(
    codigo_mensaje=CodigoMensajeCorreoEducativo.AUDIT_COPY,
).count())
print('ACTIVE_INVITATION_COUNT=', solicitud.invitaciones_continuacion.filter(
    estado=EstadoInvitacionContinuacion.ACTIVE,
).count())
```

No imprimir `token_hash`, URLs, destinatarios completos, contexto ni contenido
de mensajes. Confirmados un solo registro, estado
`PENDING_USER_REGISTRATION`, usuario no vinculado y ausencia de un proceso ya
iniciado, un administrador autorizado debe:

1. Abrir **Financiacion educativa > Solicitudes de financiacion educativa**.
2. Buscar la referencia externa y seleccionar exclusivamente esa solicitud.
3. Ejecutar **Reemitir invitacion de continuacion**.
4. Si Admin informa cooldown o limite, detenerse; no cambiar settings ni datos.
5. Permitir que `procesar_outbox_educativo` procese la nueva intencion.
6. Repetir el diagnostico anterior y confirmar mismo UUID, invitacion anterior
   `REVOKED`, entrega anterior `SUPERSEDED`, una nueva `INVITATION_REISSUE`, su
   `AUDIT_COPY` y ninguna segunda `INSTITUTIONAL_INITIAL_NOTIFICATION`.

La accion usa `reemitir_invitacion_orquestada` con el usuario administrativo
autenticado, conserva la solicitud y aplica atomicamente cooldown, limites,
revocacion e idempotencia existentes.

## Riesgos fuera de este bloque

- Un `SIGKILL` despues de escribir un archivo privado y antes de persistir su
  referencia puede dejar un archivo huerfano. Se requiere una conciliacion
  futura por antiguedad y referencias; no debe eliminarse por nombre ni durante
  una transaccion activa.
- Las pruebas JavaScript de camara caracterizan flujo y sintaxis. Falta validar
  captura real, permisos, orientacion y cambio de camara en Android/iOS.
- La concurrencia `skip_locked` se prueba solamente con PostgreSQL. SQLite no
  reproduce esa garantia y la prueba queda marcada como omitida.
