# Aviso de plan contractual disponible

Base: `origin/main` en `58092ed` (PR #12), rama independiente. No modifica los
avisos BRE-B, sus destinatarios, flags o plantillas. No envia el PDF por correo.

## Evento e idempotencia

`firma_zapsign._finalizar_firma` registra `CONTRACTUAL_PLAN_AVAILABLE` despues
de confirmar la firma y transicionar a APPROVED, dentro de la MISMA transaccion.
Usa la fotografia financiera del pagare firmado, no la configuracion vigente
del motor. No calcula condiciones nuevas ni modifica contrato, calendario o API.

La intencion queda vinculada a `OutboxCorreoEducativo.plan_contractual` y
`plan_destinatario_usuario`. Clave idempotente:
`plan-available:<solicitud_uuid>:<fotografia_uuid>`. La unicidad PostgreSQL
solicitud/version tambien impide duplicados aunque alguien use otra clave.
No se usa on_commit para crear la intencion: un rollback de la autorizacion
revierte el aviso; el webhook no envia SMTP. Si falla el registro por un error
de infraestructura, se revierte la transicion, conservando el reintento del
webhook existente. Una ausencia de destinatario se registra como FAILED, no
como excepcion que bloquee el contrato.

La ruta de descarga, consultas del portal, AppConfig.ready y arranque del worker
NO crean avisos. No hay tarea de backfill ni comando para encolar historicos.
Un proceso ya SIGNED, incluso con otro evento webhook repetido, no vuelve a
ejecutar el alta. Reemitir un pagare con la misma fotografia no es otra version
del plan y no permite otro aviso para esa fotografia.

## Destinatario y acceso

- Adulto: cuenta titular activa, asociacion `solicitud.usuario`, coincidencia
  del correo segun el control de propiedad existente y EmailAddress verificado.
- Menor: tutor responsable contractual y deudor principal verificado. Su cuenta
  DEBE tener ya el mismo acceso que exige la descarga del plan. Se reutiliza el
  verificador de destinatarios existente, sin cambiar las reglas de BRE-B.
- Una vinculacion de participante/tutor NO concede por si sola acceso al portal.
  Si el tutor pertenece a otra cuenta, se bloquea el aviso; no se reasigna el
  expediente ni se habilita acceso por coincidencia de correo o cedula.
- No se agrega CC/BCC ni copia institucional/de auditoria automatica para este
  nuevo evento: esta entrega solo notifica al destinatario elegible indicado.
  La matriz de eventos previos permanece intacta.

Motivos registrados en `codigo_ultimo_error`, sin PII en el motivo:

| Codigo | Significado |
| --- | --- |
| PLAN_RECIPIENT_NO_ACCESS | No hay cuenta activa titular con el acceso existente, o el tutor no tiene ese acceso |
| PLAN_GUARDIAN_UNDEFINED | No hay un unico tutor responsable |
| PLAN_RECIPIENT_UNVERIFIED | Correo no verificado, mayoria de edad no comprobable o destinatario no elegible |
| PLAN_RECIPIENT_CHANGED | La cuenta/correo actuales ya no son los registrados; no se reenruta |
| PLAN_VERSION_UNAVAILABLE | Calendario o autorizacion no disponibles al registrar |
| PLAN_NOTICE_SUPERSEDED | Version/autorizacion ya no disponible al entregar; pasa a SUPERSEDED sin envio |
| PLAN_NOTIFICATIONS_DISABLED | Flag apagado despues de reclamar; no se envia |

No se inventa acceso ni se bloquea APPROVED por estos motivos. Las intenciones
sin destinatario son FAILED y no se recuperan automaticamente al verificar un
correo despues: no se convierte ese cambio en un backfill de historicos.
Los codigos SMTP, leases, backoff y resolucion de ambiguedad son los del outbox
existente. AMBIGUOUS nunca habilita un reenvio ciego.

Antes de SMTP se revalida la fotografia/autorizacion/calendario, cuenta, correo
verificado y lease. Se bloquean solicitud, fotografia, usuario, verificacion y
outbox en consultas base sin joins nullable. Se mantienen esos locks hasta
terminar la entrega: los cambios de version mediante el servicio existente
serializan con el lock de solicitud. No hay sustitucion silenciosa de la version
ni del destinatario. Una version retirada/reemplazada no produce el correo viejo.

## Mensaje y configuracion

Flag NUEVO e independiente:

```dotenv
FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED=false
```

Valor predeterminado false; `.env.example` lo conserva false. Controla entrega,
no auditoria del evento: las versiones autorizadas NUEVAS desde este release
crean su intencion con el flag false, pero no se reclaman ni consumen intentos.
Antes de activarlo revisar las intenciones pendientes creadas durante la pausa.
No se crean intenciones para contratos autorizados antes de instalar el release.

Se reutilizan `BRAND_PUBLIC_BASE_URL` y `EDUCATION_EMAIL_LOGO_URL`: portal HTTPS
sin credenciales/query/fragmento y URL HTTPS del logo oficial configurado. Sin
logo o portal valido el aviso falla de forma controlada, sin enviar ni modificar
el contrato. No se agregan credenciales ni proveedores. El logo oficial actual
esta en `static/images/logo-dark.png`.

Asunto: **Tu plan contractual esta disponible | Aprobado**.
Boton: **Ver mi plan de pagos** -> ruta Django `financiacion_educativa_web:finanzas`.
El enlace no es un token ni acceso publico al PDF. La autenticacion y titularidad
se exigen en el servidor; la cuenta ajena obtiene 404 conforme al portal actual.
Contenido: el documento muestra el **calendario pactado**, no saldo vigente ni
liquidacion. Sin importes, referencias bancarias, nombres del operador, datos
de estudiante, version numerica ni adjuntos. No se promete pago, paz y salvo o
aprobacion. El pie corporativo conserva el ano de copyright del componente comun.

Muestras sinteticas revisables: `muestras/aviso-plan-contractual/`, HTML, TXT y
capturas completas 375/600 px. Las URL usan `portal.example.test`, y el UUID del
enlace es una fixture sintetica. El navegador cargo el PNG oficial desde disco,
sin solicitudes externas. No abrir estas muestras como si fueran un expediente.

## Migracion y pasos futuros de staging

Unica migracion nueva: **0036_aviso_plan_contractual**, depende de 0035 y
AUTH_USER_MODEL. Dos FK nullable PROTECT en outbox, dos choices ampliados, una
unicidad parcial y un CHECK de coherencia. Los registros existentes mantienen
NULL; no hay RunPython, reclasificacion, historicos encolados ni cambios a
tablas contractuales. No se alteran 0031/0032 ni el libro.

Despues de aprobar el PR y autorizar el despliegue, con SHA y respaldo verificados:

```bash
cd /var/www/fundetec-staging/current
staging_manage migrate --plan
staging_manage sqlmigrate financiacion_educativa 0036
staging_manage migrate financiacion_educativa
staging_manage check
```

Mantener el flag nuevo false durante instalacion. Detener primero el outbox
antiguo mediante el procedimiento autorizado: un worker de una version anterior
no conoce el nuevo tipo ni su flag. Instalar el SHA aprobado, migrar y reiniciar
los procesos web correspondientes; reiniciar outbox solo con codigo/esquema
nuevos y flag false. Coordinar conexiones durante el DDL; no mezclar workers
de releases distintos. Usar el mantenimiento existente;
no arrancar worker general ni cambiar los flags de BRE-B, IA o firma para probar
este aviso. No se ejecutaron esos comandos en ningun servidor en esta entrega.

Diagnostico agregado de solo lectura, sin destinatarios, tokens o URLs privadas:

```bash
staging_manage shell -c "
from django.conf import settings
from django.db.models import Count
from financiacion_educativa.models import OutboxCorreoEducativo
print('PLAN_EMAILS_ENABLED=', settings.FINANCIACION_EDUCATIVA_CONTRACTUAL_PLAN_EMAILS_ENABLED)
q=OutboxCorreoEducativo.objects.filter(codigo_mensaje='CONTRACTUAL_PLAN_AVAILABLE')
print(list(q.values('estado','codigo_ultimo_error').annotate(cantidad=Count('pk')).order_by('estado','codigo_ultimo_error')))
"
```

Repetir primero en PostgreSQL AISLADO, nunca con `staging_manage test` sobre la
base compartida. Usar el comando de pruebas indicado en la evidencia, con
DJANGO_LOAD_DOTENV=false, DEPLOYMENT_ENVIRONMENT=test, DATABASE_URL temporal,
correo locmem y backend de firma simulado. Para muestras, configurar
`EDU_PLAN_EMAIL_PREVIEWS` a una carpeta de evidencia sintetica y
`EDU_PLAYWRIGHT_MODULE` al Playwright local existente; no instalar proveedores.

Despues, un piloto de staging requiere autorizacion separada: adulto y menor
con tutor que YA tenga acceso, correos QA verificados, firma sandbox y expediente
nuevo. Nunca reenviar o regenerar contratos existentes para probar el aviso.
Usar un entorno/cola aislados para que solo existan casos sinteticos autorizados:
`procesar_outbox_educativo` no ofrece filtro por UUID y `--once` no garantiza que
escoja el caso deseado. NO activar el flag en staging compartido para un envio
acotado sin revisar previamente TODO el backlog de este nuevo evento y autorizar
su destino. En la cola aislada activar unicamente el flag nuevo. Verificar un aviso, enlace a login,
plan del titular, rechazo de otra cuenta, replay sin duplicados y ausencia de
adjuntos/cifras. Esta entrega no envia correos reales ni certifica ese piloto.

Reversion: detener nuevas reclamaciones apagando SOLO el flag nuevo y reiniciar
de forma controlada el proceso outbox que carga settings. Un SMTP en curso puede
terminar: conciliar SENT/AMBIGUOUS antes de cualquier recuperacion. Conservar
filas, migracion, tipos y lector actual. No borrar avisos ni revertir columnas
si ya hay intenciones; no volver a un worker que desconozca el nuevo tipo.
Los demas correos, BRE-B y las descargas siguen con su configuracion anterior.

## Evidencia local

La validacion usa PostgreSQL temporal, firma simulada y correo locmem. Cubre
adulto, tutor, falta de verificacion/acceso, cambio de cuenta, version retirada,
rollback, webhook repetido/concurrente, workers concurrentes y contratos previos
sin backfill. Resultados locales del 2026-11-13:

- PostgreSQL **16.13** temporal y aislado, sin bases/credenciales del servidor.
- Regresion: **167 casos**, **166 OK y una muestra BRE-B omitida** porque no
  estaba definido su directorio opcional. Duracion 255.782 segundos.
- Cierre: **26/26 OK**, 46.564 segundos, cero omisiones: los 25 casos completos
  del nuevo aviso mas aquella muestra BRE-B, ahora generada/renderizada solo
  en TEMP. Los conjuntos se solapan; no sumarlos como pruebas distintas.
- Tres concurrencias nuevas en PostgreSQL: registro idempotente, dos workers,
  dos webhooks simultaneos. Tambien pasan las concurrencias previas del outbox
  y avisos BRE-B. Los hilos usan sus propias conexiones y propagan excepciones.
- Muestras HTML/TXT y capturas nuevas 375/600 px con el logo oficial cargado de
  disco, sin red externa. Revision visual realizada; sin desbordamientos.
- `manage.py check`: 0 issues; `makemigrations --check --dry-run`: No changes
  detected; `migrate --plan`: sin operaciones despues de aplicar 0036 solo en
  PostgreSQL temporal. SQL revisado: dos columnas nullable/FK, indices, unicidad
  parcial y CHECK; changes de choices son no-op. Sin RunPython ni backfill.
- `node --check` y `git diff --check`: codigo 0. Las advertencias locales
  WhiteNoise/staticfiles y GLib/UWP del PDF no afectaron pruebas ni render.

Modulos de la regresion, todos con prefijo `financiacion_educativa.tests.`:
`test_aviso_plan_contractual`, `test_firma_educativa`, `test_outbox_correos`,
`test_notificaciones_pagos_breb`, `test_copias_correo_educativo`,
`test_plan_contractual_pdf`, `test_portal_estudiante`, `test_orquestacion_automatica`.
El cierre repitio el primer modulo y
`test_notificaciones_pagos_breb.NotificacionesPagosBrebTests.test_muestras_sinteticas_html_txt_y_render_sin_red`,
con `python manage.py test ... --noinput --keepdb -v1` y variables de muestras
explicitamente locales. No se ejecuto toda la suite por rutina.

No hubo SMTP real, OpenAI, ZapSign real, despliegue ni acceso a servidores.
