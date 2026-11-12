# Notificaciones de pagos BRE-B

Base: `origin/main` en `7993980`, merge del PR #7, desplegado segun Carlos.
Este bloque no cambia reglas financieras, contratos, cuotas, permisos, API,
recordatorios de vencimiento, mora, plan PDF o reparto 80/20.

## Eventos y mensaje

| Evento | Cuando se registra | Mensaje principal |
| --- | --- | --- |
| BREB_REPORT_RECEIVED | Nuevo reporte PAYER del estudiante | Recibimos tu reporte y esta pendiente de revision |
| BREB_INCOME_CONFIRMED | Nueva conciliacion, incluido registro directo OPERATIONS | Confirmamos el ingreso en el banco. Todavia no se ha aplicado a una cuota |
| BREB_PAYMENT_APPLIED | Nueva aplicacion integra a cuota | Aplicamos el pago a la cuota indicada; numero de cuota e importe aplicado |

El ingreso de Operaciones no crea un aviso de reporte del estudiante. La
aplicacion reutiliza el ingreso ya existente, sin otra conciliacion/reporte.
Cada intencion enlaza el reporte mediante FK protegida y usa la clave unica
`breb:<evento>:<reporte_uuid>`, el evento logico y Message-ID del outbox actual.
La intencion se crea dentro de la misma transaccion que el registro,
conciliacion o aplicacion. Un rollback elimina ambos. Los caminos de replay
devuelven el registro existente antes de crear notificaciones; tampoco
reconstruyen avisos historicos ausentes.

Los mensajes incluyen referencia externa de la solicitud e importe; el ultimo
incluye numero de cuota. No incluyen comprobantes, evidencia, referencias
bancarias internas, identidad del operador, enlaces privados o adjuntos. No
declaran saldo actualizado, deuda cancelada o paz y salvo. Los HTML y TXT
reutilizan la base y el componente de informacion educativos existentes.

## Destinatarios verificados

Decision explicita de Carlos para esta entrega: **en menores, el tutor
verificado es el destinatario principal**, sin copia automatica al estudiante.

| Destino | Regla |
| --- | --- |
| Adulto | Cuenta activa `solicitud.usuario`, titular segun `usuario_es_propietario_solicitud`, correo de la solicitud coincidente y EmailAddress de esa cuenta verificado |
| Menor | Unico responsable contractual de la misma solicitud, con roles GUARDIAN y PRINCIPAL_DEBTOR; correo del tutor verificado en su cuenta activa explicitamente vinculada |
| Tutor sin usuario propio | Solo puede usar la cuenta ya vinculada a la solicitud si su correo coincide con el del tutor y esta verificado en esa cuenta; no se busca otra cuenta por correo o documento |
| APROBADO | AUDIT_COPY independiente posterior a SENT cuando EDUCATIONAL_AUDIT_NOTIFICATION_EMAILS tenga destinatarios |
| Institucion | Ninguna copia de estos eventos, aunque tenga destinatarios iniciales activos en Admin |

Se usa `allauth.account.EmailAddress.verified`, sin verificar correos
artificialmente ni crear cuentas/asociaciones. La verificacion global es
opcional en el proyecto; tener una cuenta, invitacion consumida o correo
declarado no garantiza que exista ese registro verificado.

Casos no resueltos automaticamente por la matriz: edad desconocida, tutor sin
rol/deudor unico, tutor con correo distinto sin cuenta vinculada, correo no
verificado, cuenta inactiva o correo titular no coincidente. Se conserva una
fila FAILED con `PAYMENT_RECIPIENT_UNVERIFIED` y destinatarios vacios, sin
impedir registrar/conciliar/aplicar el ingreso. No se sustituye al tutor por el
estudiante ni por un correo institucional. Operaciones debe revisar el vinculo
y completar la verificacion por el procedimiento autentico de la cuenta; este
bloque no incorpora un nuevo flujo de consentimiento o verificacion.

Antes de cada envio se revalida el destinatario vigente. Una direccion ya
fijada no se redirige silenciosamente si cambia la cuenta o el tutor: queda
bloqueada. Solo un aviso inicialmente vacio puede fijar un destinatario al
recuperarlo explicitamente, despues de verificarlo. Ese snapshot se persiste
antes de SMTP y se conserva incluso si el resultado SMTP es ambiguo.

## Seguridad, orden y concurrencia

El worker reutiliza skip_locked, leases, backoff y clasificacion SMTP del
outbox. Ademas comprueba el lease vigente antes de enviar un pago. El envio
toma primero el lock de la fila base del reporte y despues el de su outbox;
conserva ambos hasta completar el intento SMTP, limitado por EMAIL_TIMEOUT.
La conciliacion/aplicacion usan ese mismo reporte como serializador. No hay
locks sobre joins nullable ni sleeps para resolver carreras.

La prueba PostgreSQL de registro directo contra conciliacion detecto un
deadlock al incorporar la FK del outbox a la solicitud. Se corrigio el orden
de confirmacion a solicitud -> reporte -> soportes -> cuenta, igual que
registro/aplicacion, antes de crear la intencion. No se quitaron locks ni
restricciones; la prueba existente ahora tambien muestra excepciones de hilos.

- Reporte ya confirmado o rechazado: aviso de recepcion SUPERSEDED.
- Ingreso ya aplicado: aviso de pendiente de aplicacion SUPERSEDED.
- Si la aplicacion gana la carrera, el aviso pendiente no sale.
- Si el envio gana el lock, termina antes de que se registre la aplicacion.
- SUPERSEDED no es SENT: no marca fecha enviada, no crea auditoria y no se
  reenvia por `--retry-failed`.
- Fallo previo a entrega: RETRYING con backoff y maximo existentes.
- Timeout/aceptacion incierta: AMBIGUOUS, nunca reenvio automatico.

No se promete entrega exactamente una vez: SMTP puede aceptar un mensaje y
perderse la confirmacion. Se conserva la intencion unica y el Message-ID,
resolviendo ambiguedades mediante el mecanismo operativo existente.

## Configuracion y migracion

Nueva variable no secreta, en settings y .env.example:

```dotenv
FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED=false
```

False no impide registrar las intenciones. Impide reclamar/enviar tanto los
originales BRE-B como sus copias de auditoria; no bloquea los demas correos
educativos. El flag se comprueba tambien despues de reclamar. Activarlo hace
elegibles las intenciones acumuladas: revisar primero el backlog, no tratarlo
como una activacion solo para futuras operaciones.

No hay proveedor, credenciales ni worker nuevo. Se conserva
`fundetec-staging-email-outbox.service` y sus controles SMTP/QA actuales.
Auditoria vacia sigue deshabilitando solo las copias de APROBADO.

Migracion nueva: `0033_notificaciones_pagos_breb`, dependiente de 0032.
Agrega FK nullable PROTECT e indice implicito al reporte, constraint de
coherencia para avisos BRE-B, y choices de los tres eventos y SUPERSEDED.
Los choices no alteran columnas; FK/indice/constraint si son SQL material.
No cambia 0031/0032, no borra filas, no genera avisos, no recorre recaudos
historicos y admite outbox existente con la nueva FK nula. Aplicarla requiere
la ventana de despliegue normal para los locks de DDL PostgreSQL.

## Staging: procedimiento posterior a revision

No ejecutado en esta entrega. No activar SMTP real como parte de instalar.

1. Revisar el SHA aprobado y respaldar por el procedimiento habitual. Detener
   el outbox antes de actualizar el codigo: un worker anterior desconoce el
   flag y los nuevos tipos. Instalar inicialmente con el flag en false.

```bash
systemctl stop fundetec-staging-email-outbox.service
# Actualizar al SHA aprobado mediante el procedimiento de releases vigente.
staging_manage migrate --plan
staging_manage migrate financiacion_educativa
staging_manage check
staging_manage shell -c "from django.conf import settings as s; assert not s.FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED; print('BREB_EMAIL_DELIVERY_DISABLED=OK')"
```

2. Reiniciar web y outbox con la misma version nueva y entorno verificado.
   No reactivar un worker con codigo anterior. La instalacion no requiere
   iniciar el worker general de automatizacion ni servicios de proveedores.

```bash
systemctl restart fundetec-staging.service
systemctl start fundetec-staging-email-outbox.service
systemctl is-active fundetec-staging-email-outbox.service
staging_manage diagnosticar_outbox_educativo
```

3. Diagnostico de solo lectura, sin correos, PII, contexto o enlaces:

```bash
staging_manage shell <<'PY'
from django.conf import settings
from django.db.models import Count, Q
from financiacion_educativa.models import OutboxCorreoEducativo
from financiacion_educativa.services.notificaciones_pagos import CODIGOS_PAGO
originales = OutboxCorreoEducativo.objects.filter(codigo_mensaje__in=CODIGOS_PAGO)
todos = OutboxCorreoEducativo.objects.filter(Q(pk__in=originales.values('pk')) | Q(correo_origen_id__in=originales.values('pk')))
print('PAYMENT_DELIVERY_ENABLED=', settings.FINANCIACION_EDUCATIVA_BREB_PAYMENT_EMAILS_ENABLED)
for fila in todos.order_by().values('tipo_evento', 'estado', 'codigo_ultimo_error').annotate(cantidad=Count('pk')).order_by('tipo_evento', 'estado'):
    print(fila)
PY
```

4. Antes de activar: confirmar cuentas/correos verificados adulto y tutor,
   revisar todos los avisos acumulados y copias; usar solo expedientes
   sinteticos. La certificacion local usa locmem, nunca SMTP. Para una prueba
   de correo en staging hace falta autorizacion separada, QA redirigido a un
   buzon controlado (`EMAIL_QA_MODE=true`, destinatario QA configurado,
   `EMAIL_LIVE_DELIVERY_ENABLED=false`) y verificar que el worker recargue el
   entorno. Recuerda que ese worker tambien procesa otros correos elegibles.
   Solo entonces activar el nuevo flag y reiniciar el outbox.
5. Verificar los tres avisos, uno por transicion; registro directo de Operaciones
   sin aviso de reporte; aplicar antes de procesar para observar SUPERSEDED;
   menor solo a tutor; ninguna copia institucional. Auditoria solo posterior a
   SENT, si configurada. No registrar ni aplicar pagos reales para certificar.

Recuperacion acotada de destinatario inicialmente no verificado: completar
primero la verificacion real y revisar el caso. Usar el comando existente con
el UUID del outbox concreto; nunca una recuperacion masiva sin revisar:

```bash
staging_manage recuperar_outbox_educativo --retry-failed --outbox-id UUID_DEL_AVISO --dry-run
# Solo despues de aprobacion expresa:
staging_manage recuperar_outbox_educativo --retry-failed --outbox-id UUID_DEL_AVISO --confirmar
```

Un destinatario aun no verificable sigue FAILED y no cuenta como recuperado.
SUPERSEDED y AMBIGUOUS no se recuperan por ese comando. No resolver una entrega
ambigua como RETRYING sin conciliar primero su resultado.

## Reversion

Apagar el flag y reiniciar web/outbox nuevos detiene avisos BRE-B y sus copias,
conservando eventos, 0033 y el resto del correo educativo. Una entrega ya
aceptada no puede deshacerse. No borrar filas ni revertir 0033 para ocultarlas.
Si hay que volver al codigo 7993980, mantener detenido el outbox: el worker
anterior no conoce el flag y podria reclamar avisos nuevos. Requiere revisar
la cola antes de volver a habilitarlo; no reenviar ni suprimir registros por SQL.

## Ejemplos y validacion local

Muestras sinteticas versionadas en `muestras/notificaciones-pagos-breb/`:
los tres mensajes en HTML, TXT y capturas completas a 375 y 600 px. No
contienen destinatarios, PII, tokens, referencias bancarias ni enlaces privados.
El render de Chromium bloquea toda red y mantiene JavaScript deshabilitado.

Validacion local del 2026-11-12: PostgreSQL 16.13 temporal en Docker, tmpfs,
sin datos ni credenciales de servidores. **201/201 OK en 205.326 segundos,
cero omisiones**. Incluye 34 pruebas nuevas de notificaciones (seis de
concurrencia PostgreSQL y el render de ejemplos) y regresiones existentes:
outbox, copias, recordatorios de continuacion, correos educativos, recaudos,
registro de Operaciones y aplicacion de cuotas. La repeticion focalizada
posterior a corregir el deadlock tambien termino 37/37 OK en 63.466 segundos.

```text
python manage.py test financiacion_educativa.tests.test_notificaciones_pagos_breb financiacion_educativa.tests.test_outbox_correos financiacion_educativa.tests.test_copias_correo_educativo financiacion_educativa.tests.test_recordatorios_correo financiacion_educativa.tests.test_correos_educativos financiacion_educativa.tests.test_recaudos_breb financiacion_educativa.tests.test_ingreso_operaciones financiacion_educativa.tests.test_aplicacion_cuotas --noinput --keepdb
```

Se uso `DJANGO_LOAD_DOTENV=false`, `DEPLOYMENT_ENVIRONMENT=test`, DEBUG=true,
DATABASE_URL del PostgreSQL local temporal, y overrides de locmem/proveedores
deshabilitados. Para regenerar muestras, `EDU_BREB_EMAIL_PREVIEWS` apunta a
la carpeta local de muestras y `EDU_PLAYWRIGHT_MODULE` al Playwright ya
instalado. El navegador es Edge/Chromium local, sin JavaScript ni red.
No ejecutar estas pruebas sobre una base compartida.

`check`: 0 issues. `makemigrations --check --dry-run`: No changes detected.
`migrate --plan` en la base temporal ya migrada: No planned migration
operations. `sqlmigrate financiacion_educativa 0033` revisado sobre PostgreSQL:
FK nullable, indice y CHECK; tres AlterField de choices no-op. El flag sin
cargar .env devolvio False. `git diff --check` y `node --check`: codigo 0,
salida vacia. El warning local de WhiteNoise por staticfiles ausente no
afecto estas pruebas ni el render de correos.

No se ejecuto la suite educativa completa por rutina. El contenedor y sus
bases temporales se eliminaron al finalizar. No se enviaron correos reales,
no se usaron proveedores ni se accedio a staging/produccion. Las pruebas
locales no certifican todavia una activacion en staging.

Revision final de muestras: se normalizaron solo espacios de fin de linea en
los HTML de ejemplo; su prueba y render se repitieron 1/1 OK en 4.065 segundos
en otro PostgreSQL temporal aislado, tambien eliminado. Sin cambios funcionales
despues de la ejecucion de 201 pruebas.
