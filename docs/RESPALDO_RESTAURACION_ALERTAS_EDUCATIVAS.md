# Respaldo, restauracion y alertas educativas

Fecha: 2026-11-13. Base: `origin/main` en
`58092ed3d4845cd473d0fe89e6715ef2e3afe61d` (merge PR #12).
Carlos confirma PR #12 desplegado, Libro 2B cerrado y avisos BRE-B activos en
produccion. El primer envio real y su recepcion siguen pendientes de verificar.
Esta entrega no consulta servidores ni cambia flags, proveedores o contratos.

## Alcance y limites

- Ensayo real de `pg_dump -Fc` / `pg_restore --exit-on-error` entre DOS PostgreSQL
  temporales, archivos privados sinteticos y aplicacion restaurada en localhost.
- Diagnostico agregado de solo lectura; no recupera leases, envia correo,
  procesa webhooks, reintenta firmas, concilia ni aplica pagos.
- Ningun comando de restauracion de este procedimiento apunta a staging
  compartido o produccion. No existe una opcion para restaurar sobre esos hosts.
- Las nuevas unidades son plantillas NO instaladas ni habilitadas. Un reporte
  en journal no acredita que alguien lo haya recibido: falta configurar el
  receptor/monitor externo de Infraestructura y comprobar su recepcion.
- No cambia modelos, migraciones, permisos, calendario, PDF, API ni reglas del
  libro. El indicador SENT no acredita entrega en buzon ni un primer envio real.

## Preparar respaldos operativos

Responsable: Infraestructura; validacion funcional: Operaciones APROBADO.
Antes de un respaldo real, aprobar ventana, retencion, cifrado y destino externo.
Propuesta para aprobar, NO un SLA certificado: respaldo diario, RPO 24 h,
ensayo mensual y despues de cambios relevantes, RTO objetivo 60 min. El ensayo
local pequeno no mide el RTO ni el volumen real de produccion.

1. Inventariar SHA, version PostgreSQL, migraciones, `FINANCIACION_EDUCATIVA_PRIVATE_ROOT`
   y cualquier otro almacenamiento privado/media realmente utilizado. La raiz
   educativa no reemplaza respaldos de otros dominios. Registrar configuracion
   de almacenamiento sin copiar secretos al ticket ni al repositorio.
2. Coordinar una ventana sin escritores, cargas, scanner, webhooks en curso ni
   workers/outbox que cambien filas/archivos. El dump es consistente a nivel BD,
   pero por si solo NO sincroniza archivos. No agregar un flag improvisado de
   mantenimiento: usar el procedimiento autorizado de Infraestructura. Este PR
   no detiene servicios ni cambia flags. Al reanudar, verificar las unidades.
3. Usar `pg_dump --format=custom --no-owner --no-acl` de la misma version mayor
   que PostgreSQL o una version compatible verificada. Credenciales desde el
   mecanismo protegido existente (pg_service/.pgpass), nunca argumentos con
   contrasenas, logs o .env copiados al bundle. Respaldar TODOS los esquemas y
   tablas de la aplicacion, incluido outbox, eventos, firmas y libro; no excluir
   las aplicaciones historicas ni reconstruirlas desde reportes.
4. Archivar la raiz privada de esa misma ventana, conservando rutas relativas.
   Generar manifiesto SHA-256 por archivo y checksums del dump y archivo. El
   manifiesto puede contener nombres sensibles: tratarlo como dato privado.
   Verificar los checksums antes de dar por valido el respaldo.
5. Cifrar dump, archivo y manifiesto con la herramienta ya aprobada de la
   organizacion ANTES de sacarlos del almacenamiento temporal protegido. Guardar
   copia fuera del servidor, con acceso restringido y retencion aprobada. No
   instalar ni elegir un proveedor de backups como efecto de este PR.
6. Custodiar por separado claves de cifrado, SECRET_KEY y claves HMAC/configuracion
   necesarias para recuperar invitaciones/firmas historicas. Un backup de datos
   no sustituye esa custodia; NO regenerar esas claves al restaurar un incidente.
   No incluir staging.env, credenciales, tokens ni claves en Git o en evidencia
   de pruebas. Limpiar plaintext temporal conforme al procedimiento del soporte.

Un backup sin restauracion comprobada, archivos o claves recuperables no es
una restauracion certificada. La ejecucion anterior es un procedimiento futuro,
no una afirmacion de que ya existen backups cifrados programados en produccion.

## Ensayo reproducible: solo datos sinteticos

Se utilizan las fixtures existentes de recaudos/aplicacion integra y PDF
sintetico. No descargar ni copiar documentos de estudiantes o respaldos reales.
El runner exige bases `test_*`, dos contenedores diferentes con etiqueta
`fundetec.purpose=restore-test`, puertos exclusivamente 127.0.0.1 y almacenamiento
tmpfs, sin volumenes del host. El destino se llama `restore_<uuid>` y se crea
nuevo; no utiliza `--clean` ni reemplaza una base existente.

Requisitos locales: Docker ya disponible, imagen `postgres:16-alpine` existente,
Python/venv y dependencias del proyecto. No instala paquetes ni llama proveedores.
Ejemplo para un host Linux AISLADO de pruebas, desde el checkout del PR:

```bash
bash <<'BASH'
set -euo pipefail
umask 077
# Definir antes PYTHON con el venv del checkout local, no un ejecutable remoto.
: "${PYTHON:?Defina ruta absoluta del Python local de pruebas}"
SOURCE=fundetec-restore-source-test
TARGET=fundetec-restore-target-test
for nombre in "$SOURCE" "$TARGET"; do
  if docker inspect "$nombre" >/dev/null 2>&1; then
    echo 'Nombre ocupado: no reutilizar ni eliminar un contenedor ajeno'; exit 1
  fi
done
PASSWORD=$(openssl rand -hex 24)
SRC=''
DST=''
limpiar() {
  test -z "$DST" || docker rm -f --volumes "$DST" >/dev/null
  test -z "$SRC" || docker rm -f --volumes "$SRC" >/dev/null
  unset PASSWORD
}
trap limpiar EXIT
SRC=$(docker run --pull=never -d --name "$SOURCE" --label fundetec.purpose=restore-test \
  -p 127.0.0.1:55455:5432 --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=restore_test -e POSTGRES_PASSWORD="$PASSWORD" \
  -e POSTGRES_DB=restore_source postgres:16-alpine)
DST=$(docker run --pull=never -d --name "$TARGET" --label fundetec.purpose=restore-test \
  -p 127.0.0.1:55456:5432 --tmpfs /var/lib/postgresql/data \
  -e POSTGRES_USER=restore_test -e POSTGRES_PASSWORD="$PASSWORD" \
  -e POSTGRES_DB=restore_target postgres:16-alpine)
for cid in "$SRC" "$DST"; do
  for n in $(seq 1 30); do
    docker exec "$cid" pg_isready -U restore_test && break
    sleep 1
  done
  docker exec "$cid" pg_isready -U restore_test
done
env -i PATH="$PATH" HOME=/tmp PYTHONDONTWRITEBYTECODE=1 \
  DJANGO_LOAD_DOTENV=false DEBUG=true DEPLOYMENT_ENVIRONMENT=test \
  DATABASE_URL="postgresql://restore_test:$PASSWORD@127.0.0.1:55455/restore_source" \
  EDU_RESTORE_ALLOW_ISOLATED=true EDU_RESTORE_DOCKER_SOURCE="$SRC" \
  EDU_RESTORE_DOCKER_TARGET="$DST" \
  "$PYTHON" manage.py test \
  financiacion_educativa.tests.test_diagnostico_operativo \
  financiacion_educativa.tests.test_restauracion_operativa \
  financiacion_educativa.tests.test_outbox_correos \
  financiacion_educativa.tests.test_notificaciones_pagos_breb \
  financiacion_educativa.tests.test_firma_educativa --noinput -v2
BASH
```

No ejecutar este ejemplo sobre Docker compartido sin autorizacion de su
operador. Si puertos/nombres estan ocupados o falta Docker, detener; nunca
cambiar el DSN por el de staging/produccion. Las variables `EDU_RESTORE_*` son
exclusivas del runner; NO agregarlas a staging.env ni a unidades de negocio.
En Windows usar el venv local del proyecto y definir las mismas variables solo
en el proceso PowerShell; los helpers son multiplataforma y los pg_* se ejecutan
dentro de los contenedores. No se carga `.env`.

### Lo que prueba la restauracion

1. Crear contrato sintetico coherente, archivos privados SAFE y dos ingresos:
   uno aplicado a la primera cuota y otro confirmado sin aplicar.
2. `pg_dump` de la base test_ del runner y archivo privado en TemporaryDirectory
   fuera del repositorio. Verificar manifiesto, rechazar traversal/enlaces y
   extraer solo a una ruta nueva. No reutiliza el directorio privado de origen.
3. Copiar dump al segundo contenedor, `createdb restore_<uuid>` y
   `pg_restore --exit-on-error --no-owner --no-privileges` en esa base nueva.
   Los IDs, nombres y DSN de destino se validan antes de cualquier restauracion.
4. Arrancar Django local con esa base y archivos restaurados, puerto HTTP
   efimero, integraciones deshabilitadas y correo locmem. Comprobar health 200.
5. En otro proceso, leer el estado contractual APPROVED/autorizacion/financial_terms,
   calendario y hash del pagare sintetico. El titular descarga su comprobante;
   otra cuenta recibe 404 y el acceso del estudiante a evidencia bancaria 403.
6. Cuadrar por ingreso y agregado: recibido = capital + intereses + sin destino.
   No duplicar aplicacion historica, no cambiar contrato ni crear pagos nuevos
   durante la verificacion. La sesion del cliente de prueba solo pertenece al
   destino efimero.
7. Detener app hija, eliminar base restore_ y dumps de ambos contenedores,
   limpiar archivos temporales y finalmente contenedores/volumenes creados.

El test guarda solo evidencia agregada y tiempos. No imprime correos, cedulas,
rutas privadas, URLs de firma, datos bancarios o claves. Si falla el probe,
informa tipo de excepcion y linea del verificador, no payloads completos.
No es una validacion juridica/criptografica de una firma real ni un RTO productivo.

## Alertas accionables

Comando, sin procesamiento ni efectos secundarios:

```text
python manage.py diagnosticar_operacion_educativa --servicio UNIDAD_WEB.service --servicio UNIDAD_OUTBOX.service --servicio UNIDAD_WORKER_REQUERIDA.service --servicio UNIDAD_SCANNER_REQUERIDA.service
```

Los nombres se contrastan con el servidor; no se inventan unidades productivas
ni se exige un worker que este detenido intencionadamente por su plan operativo.
Pueden incluirse timers realmente requeridos mediante `--servicio NOMBRE.timer`.
Nombres limitados y sin rutas/opciones; usa subprocess sin shell y timeout de
5 segundos por unidad. No recibe URLs ni hace llamadas HTTP a proveedores.
Sin unidades emite `SERVICE_SCOPE_MISSING`, nunca un OK de disponibilidad.

| Codigo | Umbral inicial operativo | Responsable | Primer diagnostico, SIN recuperacion |
| --- | --- | --- | --- |
| SERVICE_DOWN | Una unidad requerida no activa | Infraestructura de guardia | systemctl status y journal de esa unidad; contrastar mantenimiento autorizado |
| SERVICE_CHECK_UNAVAILABLE | Unidad ausente, timeout o consulta no verificable | Infraestructura de guardia | Verificar nombre, permisos y systemd; no asumir que esta activa |
| OUTBOX_OVERDUE | PENDING/RETRYING con proxima ejecucion vencida >= 15 min | Soporte APROBADO | Servicio outbox, flags observados, motivo y proxima ejecucion en Admin restringido |
| OUTBOX_LEASE_EXPIRED | SENDING con lease vencido, un caso | Soporte + Infraestructura | Consultar lease y registros SMTP antes de recuperar; podria haberse entregado |
| OUTBOX_FAILED | Un FAILED, incluyendo historicos no resueltos | Soporte APROBADO | Codigo de error en Admin restringido y ticket; no reenviar ciegamente |
| OUTBOX_AMBIGUOUS | Un AMBIGUOUS | Soporte APROBADO | Conciliar Message-ID con SMTP/proveedor; no reenviar sin resolver |
| SIGNATURE_QUEUE_OVERDUE | PENDING >= 15 min | Operaciones APROBADO | Revisar etapa/autorizacion del expediente y cola, sin invocar envio |
| SIGNATURE_SEND_STALLED | SENDING >= 10 min | Operaciones + Infraestructura | Estado/external_id restringido y diagnostico del proveedor antes de otro envio |
| SIGNATURE_AMBIGUOUS | FAILED con SIGNATURE_SEND_AMBIGUOUS | Operaciones APROBADO | Conciliacion externa por responsable autorizado, nunca reenvio automatico |
| SIGNATURE_FAILED | Otro FAILED | Operaciones APROBADO | Codigo y evidencia tecnica en acceso restringido, sin regenerar contrato |
| SIGNATURE_WAIT_OVERDUE | SENT >= 72 h | Operaciones APROBADO | Distinguir espera del firmante de webhook pendiente; no es rechazo |
| SIGNATURE_WEBHOOK_OVERDUE | RECEIVED/RETRYABLE_ERROR >= 15 min | Operaciones + Infraestructura | Revisar evento y resultado, no autorizar el curso manualmente |
| DATABASE_DIAGNOSTIC_UNAVAILABLE | Fallo de lectura BD | Infraestructura de guardia | Disponibilidad/permisos SELECT; no restaurar ni escribir desde el monitor |

Los umbrales de aviso/cola/webhook se ajustan con `--aviso-minutos`; envio con
`--firma-envio-minutos`; espera con `--firma-espera-horas`. Son umbrales de
observacion, NO cadencias comerciales, vencimientos ni parametros de reintento.
15 min cuentan desde proxima ejecucion, no creacion: un recordatorio futuro no
alerta por ser antiguo. SENT/SUPERSEDED no son pendientes. En firmas se excluyen
artefactos no vigentes y estados terminales; una espera larga no prueba fallo.
FAILED/AMBIGUOUS historicos siguen visibles hasta resolverlos mediante el
procedimiento existente y autorizacion correspondiente; no se ocultan por edad.

En PostgreSQL todas las consultas del diagnostico van en una transaccion
REPEATABLE READ, READ ONLY. Las metricas son conteos/edades/estados; no contiene
IDs de estudiantes, payload, texto de errores del proveedor, destinatarios,
referencias, tokens ni rutas. Codigos de salida: **0 OK; 2 alertas; 3 diagnostico
incompleto/validacion propia de argumentos**. Los errores de sintaxis del parser
Django tambien pueden salir con 2 sin JSON: tratar ausencia de JSON como
diagnostico incompleto, nunca como OK. La ausencia de lectura BD o de chequeo de
servicios no se interpreta como saludable. No leer stderr sin sanitizar.

## Programacion e instrucciones de Operaciones

Plantillas versionadas:
`deploy/systemd/fundetec-educational-monitor.service.template` y
`fundetec-educational-monitor.timer`.

Despues de aprobar el PR y autorizar instalacion, Infraestructura debe:

1. Contrastar User, Group, WorkingDirectory, ruta del Python, EnvironmentFile y
   nombres requeridos con las unidades REALES. Sustituir TODOS los marcadores
   `@@...@@`; `@@SERVICE_ARGUMENTS@@` contiene solo pares `--servicio unidad`
   validados. Conservar el entorno existente; no cambiar flags ni proveedores.
2. Guardar la unidad renderizada fuera del repositorio, con nombre
   `fundetec-educational-monitor.service`. Verificar que no queden marcadores,
   ejecutar `systemd-analyze verify` sobre service/timer renderizados. Sin
   errores, copiar a /etc/systemd/system y `systemctl daemon-reload`.
3. Instalar inicialmente **disabled/stopped**:

```bash
# RENDERED_DIR contiene ambos archivos, ya revisados, con sus nombres finales.
: "${RENDERED_DIR:?Defina el directorio de las unidades revisadas}"
test -f "$RENDERED_DIR/fundetec-educational-monitor.service"
test -f "$RENDERED_DIR/fundetec-educational-monitor.timer"
if grep -q '@@' "$RENDERED_DIR/fundetec-educational-monitor.service"; then
  echo 'ERROR: faltan sustituciones'; exit 1
fi
systemd-analyze verify "$RENDERED_DIR/fundetec-educational-monitor.service" \
  "$RENDERED_DIR/fundetec-educational-monitor.timer"
# Si ya existen estas unidades, respaldarlas primero en el directorio de
# cambios autorizado y conservar su enable/active anterior para la reversion.
install -m 0644 "$RENDERED_DIR/fundetec-educational-monitor.service" \
  /etc/systemd/system/fundetec-educational-monitor.service
install -m 0644 "$RENDERED_DIR/fundetec-educational-monitor.timer" \
  /etc/systemd/system/fundetec-educational-monitor.timer
systemctl daemon-reload
systemctl disable --now fundetec-educational-monitor.timer
systemctl stop fundetec-educational-monitor.service
systemctl status fundetec-educational-monitor.timer --no-pager
```

4. Con autorizacion operativa, ejecutar una observacion manual con los nombres
   reales. Revisar JSON/exit code. El servicio puede quedar failed por una alerta:
   NO es una orden de reenvio ni de reinicio. El monitor no tiene Restart.
5. Registrar responsable nominal, suplente, canal de recepcion y deduplicacion
   por codigo/unidad. El sistema de observabilidad existente debe recoger el
   journal/codigo de salida y alertar tambien si el monitor deja de ejecutarse
   durante > 10 min. Este chequeo externo evita que el monitor sea su propio
   unico vigilante. Probar recepcion con servicio ficticio solo en pruebas.
6. Solo tras comprobar recepcion y autorizar: `systemctl enable --now
   fundetec-educational-monitor.timer`. Cada cinco minutos activa el mismo
   oneshot; systemd no inicia dos instancias simultaneas de esa unidad. Las
   unidades existentes de negocio no se reinician ni modifican.

Consulta:

```bash
systemctl list-timers fundetec-educational-monitor.timer --all
systemctl status fundetec-educational-monitor.service --no-pager
journalctl -u fundetec-educational-monitor.service --since '30 minutes ago' --no-pager
```

Reversion del monitor: deshabilitar/detener timer, detener oneshot, retirar SOLO
sus dos unidades si fue primera instalacion o reponer la copia previa si fue
actualizacion; daemon-reload. Conservar evidencia/tickets y el procedimiento.
No cambiar servicio web/outbox/worker/scanner, flags, BD ni registros de pagos.

### Primer aviso BRE-B real (pendiente)

No generar un pago ficticio en produccion para validarlo. Ante la primera
transicion REAL y autorizada, Soporte verifica una sola intencion del evento y
SENT en Admin restringido, sin duplicados ni aviso superado; concilia Message-ID
con el proveedor y confirma recepcion con el destinatario verificado (tutor
principal si corresponde). Registrar en ticket hora/evento/resultado sanitizado,
sin correo completo ni soporte. Actualizar roadmap solo despues de esa prueba.
Una prueba sintetica o conteo SENT no permite marcar este hito como cumplido.

## Evidencia del ensayo local

El ensayo aislado ya verifico arranque HTTP 200, siete archivos restaurados,
calendario y hash del pagare sintetico intactos, titularidad/privacidad y cuadre:
**151.528 recibidos = 90.101 capital + 11.427 intereses + 50.000 sin destino**.
El ingreso de 50.000 no se presenta como cuota pagada. Version PostgreSQL,
repeticion final, cantidades y tiempos:

- PostgreSQL **16.13** (Docker local tmpfs, dos contenedores distintos).
- Regresion final de los cinco modulos indicados: **83/83 OK**, **321.661 s**,
  cero omisiones. Incluye los 23 casos nuevos, concurrencias de outbox/avisos y
  render de muestras HTML/TXT en navegador local sin red; muestras fuera de Git.
- Ultimo ensayo: dump **643.743 bytes**, siete archivos, **8.641 s** desde inicio
  de respaldo hasta probe verificado, HTTP 200, contrato/autorizacion/calendario
  y hash del pagare sintetico iguales. Una sola aplicacion historica, ningun
  crecimiento del outbox al arrancar la copia. No representa volumen real.
- Se corrigieron exclusivamente fixtures/verificador nuevos durante su cierre
  (lease consistente, acceso al objeto de resultado y configuracion aislada de
  hosts); no se relajaron constraints ni se modifico el flujo productivo.
- `systemd-analyze` no esta disponible en el host Windows de este ensayo.
  La inspeccion automatizada de nombres, ExecStart, usuario, entorno, timer y
  protecciones pasa; verify del render definitivo sigue siendo obligatorio en
  Linux ANTES de instalar. No se afirma que las plantillas esten instaladas.
- Las advertencias locales WhiteNoise/staticfiles y GLib/UWP del PDF no
  afectaron los resultados. No hubo correo real ni llamadas a proveedores.
- `manage.py check`: 0 issues. `makemigrations --check --dry-run`: No changes
  detected. `migrate --plan`: No planned migration operations, despues de
  aplicar exclusivamente las migraciones existentes en la base temporal.
  `git diff --check`: codigo 0. Tres bloques del procedimiento comprobados con
  Git Bash `bash -n` por stdin, sin ejecutarlos. No hay migraciones nuevas.

Limitaciones pendientes: rutas/unidades reales contrastadas por Infraestructura,
backup cifrado externo y custodia probados con su herramienta autorizada,
volumen/RTO reales, responsables nominales y recepcion efectiva de alertas.
Ninguna de esas tareas se presume ejecutada por este ensayo local.
