# Activacion del canal BRE-B educativo

Revisado localmente: 2026-11-04, base main
`300bf42bef117c85a1d31be6fa993736371c59d8`, arbol inicialmente limpio.
El usuario informa ese ultimo despliegue, infraestructura instalada y canal
productivo deshabilitado. No se accedio a servidores para corroborar sus SHA,
migraciones, variables, receptor o permisos. Esto no es una activacion realizada.

## Que se puede habilitar

Acceso autenticado a una obligacion realmente firmada/autorizada -> datos del
receptor -> QR/llave -> transferencia en la app bancaria -> reporte y comprobante
privados -> ClamAV -> evidencia bancaria independiente -> conciliacion manual.
El resultado es **Ingreso confirmado pendiente de aplicacion**, no cuota pagada,
capital amortizado, liquidacion de deuda ni paz y salvo. No se implementa un banco
de pagos ni se consulta una API bancaria para verificar ingresos.

Ruta de entrada: `/financiacion-educativa/mis-solicitudes/continuar/`, solo para
solicitudes ya asociadas al usuario. APPROVED dirige a finanzas; alli aparece
**Pagar y reportar transferencia** cuando el canal esta habilitado y la firma es
elegible. Tambien existe el enlace en resultado. No se permite acceso por conocer
el UUID, ser staff o compartir correo. Las simulaciones Sandbox no son elegibles.

## Datos que deben confirmarse antes de produccion

- Banco e identificador canonico de la cuenta: numero completo y tipo de cuenta
  contrastados con el banco. No crear nombres alternativos para el mismo banco/
  cuenta; la unicidad del movimiento depende de esa identidad estable.
- Titular legal autorizado para recibir, llave BRE-B vigente, QR oficial emitido
  por el banco y confirmacion humana de que QR, llave y cuenta corresponden.
  El antivirus y el hash no certifican titularidad ni decodifican el QR.
- Cuenta individual del conciliador, responsabilidad aprobada, permisos y acceso
  al extracto bancario independiente. Nunca ingresar claves bancarias en la app.
- Nombre exacto y caracter de unicidad de la referencia del extracto bancario.
  No confundirla con la referencia que declara el estudiante, que no es prueba.
- Usuario del configurador y responsable de recuperacion del antivirus.
- Dominio, rutas, usuario/grupo, servicio web, servicio de escaneo y archivo de
  entorno reales de cada ambiente; SHA y migraciones aplicados; respaldo probado.
- Almacenamiento privado compartido entre web/scanner, permisos de lectura/escritura,
  acceso del usuario del scanner al socket ClamAV y limites de carga del proxy.
- Soporte al usuario, tiempo operativo de conciliacion y procedimiento de incidentes.
  No prometer fechas automaticas ni confirmacion bancaria instantanea.

Sin esos datos no se certifica la activacion productiva. No usar el receptor
sintetico NO TRANSFERIR de staging ni copiar una llave de otro proyecto.

## Permisos exactos

Asignar desde Django Admin a cuentas individuales autorizadas, no a todos los
staff ni a una cuenta compartida. `is_staff` no reemplaza estos permisos:

| Funcion | Permisos de financiacion_educativa |
| --- | --- |
| Consultar y conciliar | acceder_dashboard_operativo, consultar_recaudos_educativos, conciliar_recaudos_educativos |
| Solo consultar | acceder_dashboard_operativo, consultar_recaudos_educativos |
| Versionar y activar QR en Admin | configurar_receptor_breb, view_receptorbrebeducativo; cuenta con acceso legitimo a Admin |
| Reintentar un escaneo manual | escanear_comprobantes_educativos |

El conciliador no necesita ser superusuario ni acceso general a Django Admin.
La evidencia BANK_EVIDENCE debe pertenecer al mismo operador que confirma;
no compartir cuentas ni reasignar evidencia por SQL. Puede reutilizar su archivo
seguro para varios movimientos, pero debe verificar cada referencia por separado.

## Configuracion conocida

Mantener inicialmente:

```dotenv
FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=false
FINANCIACION_EDUCATIVA_RECAUDO_MAX_BYTES=10485760
FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND=financiacion_educativa.services.escaneo_documentos.ClamAVDocumentScanBackend
FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS=false
FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET=/run/clamav/clamd.ctl
FINANCIACION_EDUCATIVA_CLAMAV_HOST=
FINANCIACION_EDUCATIVA_CLAMAV_PORT=3310
FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS=3
FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS=30
FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS=3
FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS=300
FINANCIACION_EDUCATIVA_PRIVATE_STORAGE_BACKEND=financiacion_educativa.storage.PrivateFileSystemStorage
```

PRIVATE_ROOT debe ser la ruta privada real del ambiente, nunca static/media ni
una ruta servida por Nginx. Socket/host anteriores son los valores documentados,
no una comprobacion del servidor. Para TCP, configurar host autorizado y vaciar
UNIX_SOCKET; no exponer clamd a Internet. No activar IA, ZapSign, automatizacion
documental o email para cerrar este canal: son recorridos independientes.
No requiere token bancario, OpenAI o ePayco. No cambiar otras flags ya aprobadas.

## Comandos de servidor: diagnostico previo

Todos los comandos siguientes son para una sesion administrativa futura y
autorizada. No se ejecutaron en esta revision. Nunca usar `set -x`, imprimir
el entorno completo ni pegar secretos en comandos, capturas o informes.

### Staging

Reutilizar la funcion `staging_manage` de
`docs/interno/operaciones/MANTENIMIENTO_STAGING.md`, que carga exclusivamente
`/var/www/fundetec-staging/shared/staging.env` como `fundetec-staging`.

```bash
cd /var/www/fundetec-staging/current
sudo -u fundetec-staging git rev-parse HEAD
sudo -u fundetec-staging git status --short
staging_manage showmigrations financiacion_educativa instituciones
staging_manage migrate --plan
staging_manage check
systemctl show fundetec-staging.service fundetec-staging-receipts-scan.service \
  -p LoadState -p ActiveState -p User -p Group -p WorkingDirectory -p EnvironmentFiles -p ReadWritePaths
```

### Produccion: identificar sin inventar rutas

Existe una plantilla productiva parametrizada:
`deploy/systemd/fundetec-production-receipts-scan.service.template`.
No prueba que este instalada. Carlos confirma el despliegue 300bf42 y soporte de
ingresos opcional activo en Jowalth; son antecedentes operativos, no una inspeccion
del servidor en esta revision. Verificar la unidad real antes de usar sus valores.
No instalar la plantilla de staging en produccion.

```bash
systemctl list-unit-files --type=service --no-pager | grep -Ei 'fundetec|aprobado|receipt'
read -r -p 'Unidad web productiva verificada: ' WEB_UNIT
read -r -p 'Unidad scanner productiva verificada: ' SCAN_UNIT
read -r -p 'Operacion autorizada (new/update): ' INSTALL_MODE
case "$INSTALL_MODE" in new|update) ;; *) echo 'Operacion invalida'; exit 1 ;; esac
systemctl show "$WEB_UNIT" -p LoadState -p ActiveState \
  -p User -p Group -p WorkingDirectory -p EnvironmentFiles -p ReadWritePaths
if [ "$INSTALL_MODE" = update ]; then
  systemctl show "$SCAN_UNIT" -p LoadState -p ActiveState \
    -p User -p Group -p WorkingDirectory -p EnvironmentFiles -p ReadWritePaths
fi
read -r -p 'Ruta de codigo productivo confirmada: ' APP_DIR
read -r -p 'Python absoluto del venv productivo: ' VENV_PYTHON
read -r -p 'Archivo de entorno productivo confirmado: ' ENV_FILE
read -r -p 'Usuario del servicio confirmado: ' SERVICE_USER
test -n "$APP_DIR" && test -n "$VENV_PYTHON" && test -n "$ENV_FILE" && test -n "$SERVICE_USER"
test -d "$APP_DIR/.git" && test -x "$VENV_PYTHON" && test -r "$ENV_FILE"
test "$(systemctl show "$WEB_UNIT" -p LoadState --value)" = loaded
if [ "$INSTALL_MODE" = update ]; then
  test "$(systemctl show "$SCAN_UNIT" -p LoadState --value)" = loaded
else
  test "$(systemctl show "$SCAN_UNIT" -p LoadState --value)" = not-found
  test ! -e "/etc/systemd/system/$SCAN_UNIT"
  test ! -L "/etc/systemd/system/$SCAN_UNIT"
  test ! -e "/etc/systemd/system/$SCAN_UNIT.d"
fi
test "$(systemctl show "$WEB_UNIT" -p User --value)" = "$SERVICE_USER"
if [ "$INSTALL_MODE" = update ]; then
  test "$(systemctl show "$SCAN_UNIT" -p User --value)" = "$SERVICE_USER"
fi
test "$(systemctl show "$WEB_UNIT" -p WorkingDirectory --value)" = "$APP_DIR"
if [ "$INSTALL_MODE" = update ]; then
  test "$(systemctl show "$SCAN_UNIT" -p WorkingDirectory --value)" = "$APP_DIR"
fi
```

Ejecutar las comprobaciones como bloque en `bash` con `set -euo pipefail`, no
continuar si alguna falla; confirmar ademas EnvironmentFiles, grupo, comando
continuo del scanner, permisos y ReadWritePaths contra los valores anteriores.
En update, si no esta cargada la unidad real o los usuarios difieren, detenerse.
En new, aprobar el nombre y comprobar que ningun otro servicio, cron, contenedor
o proceso ejecuta el mismo scanner. Una unidad not-found no prueba por si sola
que no existe otro scanner.

### Preparar la plantilla productiva (solo despues del contraste anterior)

Estos comandos son un procedimiento futuro para el servidor, no acciones realizadas
en local. Ejecutar en Bash como root, con `set -euo pipefail` y las variables
verificadas del bloque anterior. En update, `SCAN_UNIT` es la unidad existente;
en new, el nombre nuevo aprobado. Nunca crear un segundo scanner paralelo.
Contrastar ademas `PRIVATE_ROOT`
con `settings.FINANCIACION_EDUCATIVA_PRIVATE_ROOT`, los permisos y el socket/host de ClamAV.
El archivo env debe ser compatible tambien con `EnvironmentFile` de systemd,
sin `export`, sustituciones de shell ni referencias a otras variables.

```bash
read -r -p 'Grupo del servicio verificado: ' SERVICE_GROUP
read -r -p 'FINANCIACION_EDUCATIVA_PRIVATE_ROOT absoluto verificado: ' PRIVATE_ROOT
if [ "$INSTALL_MODE" = update ]; then
  test "$(systemctl show "$SCAN_UNIT" -p Group --value)" = "$SERVICE_GROUP"
fi
id "$SERVICE_USER"
getent group "$SERVICE_GROUP" > /dev/null
test -d "$PRIVATE_ROOT"
runuser -u "$SERVICE_USER" -- test -r "$PRIVATE_ROOT"
runuser -u "$SERVICE_USER" -- test -w "$PRIVATE_ROOT"
export SERVICE_USER SERVICE_GROUP APP_DIR ENV_FILE VENV_PYTHON PRIVATE_ROOT SCAN_UNIT INSTALL_MODE
PREPARED=$(mktemp -d /tmp/fundetec-receipts-unit.XXXXXX)
export PREPARED
"$VENV_PYTHON" - <<'PY'
import os, pathlib, re
values = {name: os.environ[name] for name in (
    'SERVICE_USER', 'SERVICE_GROUP', 'APP_DIR', 'ENV_FILE', 'VENV_PYTHON', 'PRIVATE_ROOT')}
for name, value in values.items():
    pattern = r'[a-z_][a-z0-9_-]*' if name.startswith('SERVICE_') else r'/[A-Za-z0-9_./-]+'
    assert re.fullmatch(pattern, value), f'Valor no compatible con plantilla: {name}'
unit = os.environ['SCAN_UNIT']
assert re.fullmatch(r'[A-Za-z0-9_-]+\.service', unit)
source = pathlib.Path(values['APP_DIR']) / 'deploy/systemd/fundetec-production-receipts-scan.service.template'
text = source.read_text()
for name, value in values.items():
    text = text.replace('@@' + name + '@@', value)
assert '@@' not in text
(pathlib.Path(os.environ['PREPARED']) / unit).write_text(text)
PY
systemd-analyze verify "$PREPARED/$SCAN_UNIT"
if [ "$INSTALL_MODE" = update ]; then systemctl cat "$SCAN_UNIT"; fi
```

Revisar el resultado, confirmar que no hay otro scanner equivalente y que no hay
drop-ins que cambien ExecStart, entorno, rutas o protecciones. Detenerse si existen
diferencias sin resolver. La plantilla solo permite escribir en PRIVATE_ROOT;
PostgreSQL y ClamAV se acceden por sockets/red, no necesitan rutas de escritura
adicionales. `PrivateTmp` permite temporales privados. No concede acceso a media
publica ni al codigo. El comando es continuo, responde SIGTERM y conserva leases.

Instalacion autorizada posterior, inicialmente detenida y sin habilitar al arranque:

```bash
BACKUP_DIR=$(mktemp -d /root/fundetec-receipts-backup.XXXXXX)
printf '%s\n' "$INSTALL_MODE" > "$BACKUP_DIR/install-mode.txt"
if [ "$INSTALL_MODE" = update ]; then
  test "$(systemctl show "$SCAN_UNIT" -p FragmentPath --value)" = "/etc/systemd/system/$SCAN_UNIT"
  test ! -L "/etc/systemd/system/$SCAN_UNIT"
  systemctl is-enabled "$SCAN_UNIT" > "$BACKUP_DIR/enabled-before.txt" || true
  systemctl is-active "$SCAN_UNIT" > "$BACKUP_DIR/active-before.txt" || true
  cp -a "/etc/systemd/system/$SCAN_UNIT" "$BACKUP_DIR/$SCAN_UNIT"
  systemctl disable --now "$SCAN_UNIT"
else
  test "$(systemctl show "$SCAN_UNIT" -p LoadState --value)" = not-found
  test ! -e "/etc/systemd/system/$SCAN_UNIT"
  test ! -L "/etc/systemd/system/$SCAN_UNIT"
fi
install -o root -g root -m 0644 "$PREPARED/$SCAN_UNIT" "/etc/systemd/system/$SCAN_UNIT"
systemd-analyze verify "/etc/systemd/system/$SCAN_UNIT"
systemctl daemon-reload
systemctl is-enabled "$SCAN_UNIT" || true
systemctl is-active "$SCAN_UNIT" || true
```

Con recaudos false, `ExecCondition` sale 1 sin iniciar Django, sin llamadas y sin
bucle de reinicio. Rechaza tambien ambientes distintos de production. No escanea
el QR previo con este worker: usar el comando acotado con actor y motivo descrito
abajo. Tras certificar QR, permisos, ClamAV y staging, activar el flag en el entorno
productivo con autorizacion, reiniciar web, iniciar y comprobar el scanner:

```bash
systemctl start "$SCAN_UNIT"
systemctl status "$SCAN_UNIT" --no-pager
journalctl -u "$SCAN_UNIT" --since '5 minutes ago' --no-pager
systemctl show "$SCAN_UNIT" -p User -p Group -p WorkingDirectory -p EnvironmentFiles -p ExecStart -p ReadWritePaths
# Solo despues de comprobar funcionamiento continuo:
systemctl enable "$SCAN_UNIT"
```

Rollback: deshabilitar canal y reiniciar web primero, sin borrar datos ni evidencia.
Conservar BACKUP_DIR y PREPARED hasta aprobar. En update restaurar la unidad previa;
en new retirar solo la unidad instalada aqui. Si existen drop-ins no resueltos,
detenerse antes de instalar o revertir.

```bash
systemctl disable --now "$SCAN_UNIT"
test ! -L "/etc/systemd/system/$SCAN_UNIT"
test ! -e "/etc/systemd/system/$SCAN_UNIT.d"
cmp -s "$PREPARED/$SCAN_UNIT" "/etc/systemd/system/$SCAN_UNIT"
if [ "$(cat "$BACKUP_DIR/install-mode.txt")" = update ]; then
  install -o root -g root -m 0644 "$BACKUP_DIR/$SCAN_UNIT" "/etc/systemd/system/$SCAN_UNIT"
  systemd-analyze verify "/etc/systemd/system/$SCAN_UNIT"
elif [ "$(cat "$BACKUP_DIR/install-mode.txt")" = new ]; then
  rm -- "/etc/systemd/system/$SCAN_UNIT"
else
  echo 'Modo de respaldo invalido'; exit 1
fi
systemctl daemon-reload
systemctl reset-failed "$SCAN_UNIT" || true
# Update: consultar enabled-before.txt y active-before.txt del respaldo.
# Restaurar enable/start solo tras autorizacion y segun esos estados previos.
# New: debe permanecer inexistente; no habilitar ni iniciar otra unidad.
```

No reactivar el scanner anterior si no es seguro con el flag false. Si la unidad
previa estaba masked, linked, en otra ruta o tenia overrides, detenerse antes
de instalar: requiere un plan especifico, no esta sustitucion simple.

Wrapper temporal de consola, siguiendo el patron real de staging; el archivo
de entorno debe ser compatible con Bash y estar protegido con permisos adecuados:

```bash
production_manage() {
  runuser -u "$SERVICE_USER" -- env -i HOME=/tmp LANG=C.UTF-8 \
    PATH="$(dirname "$VENV_PYTHON"):/usr/bin:/bin" \
    bash -c '
      set -Eeuo pipefail
      set -a
      . "$1"
      set +a
      export PYTHONDONTWRITEBYTECODE=1
      cd "$2"
      shift 2
      python_bin="$1"
      shift
      exec "$python_bin" manage.py "$@"
    ' bash "$ENV_FILE" "$APP_DIR" "$VENV_PYTHON" "$@"
}
sudo -u "$SERVICE_USER" git -C "$APP_DIR" rev-parse HEAD
sudo -u "$SERVICE_USER" git -C "$APP_DIR" status --short
production_manage shell -c 'from django.conf import settings as s; assert s.DEPLOYMENT_ENVIRONMENT == "production"; assert not s.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED; print("PREACTIVACION_PRODUCTIVA=OK")'
production_manage showmigrations financiacion_educativa instituciones
production_manage migrate --plan
production_manage check
```

El wrapper no modifica archivos. No usarlo hasta verificar que apunta a produccion;
un path correcto por si solo no garantiza el ambiente. Si las variables systemd
se suministran por otros mecanismos, utilizar el wrapper oficial del servidor.

### Diagnostico Django sin secretos (ambos ambientes)

En staging definir `MANAGE=staging_manage`; en produccion `MANAGE=production_manage`.
Las funciones anteriores deben existir en la misma sesion Bash.

```bash
"$MANAGE" shell -c '
import os
from django.conf import settings as s
from django.db import connection
from financiacion_educativa.models_recaudos import ReceptorBrebEducativo, ArchivoRecaudoEducativo
from financiacion_educativa.services.recaudos import receptor_vigente
print("ENVIRONMENT=", s.DEPLOYMENT_ENVIRONMENT)
print("DB_VENDOR=", connection.vendor)
print("CHANNEL_ENABLED=", s.FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED)
print("SCAN_BACKEND=", s.FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND)
print("TEST_SCAN_ALLOWED=", s.FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS)
print("PRIVATE_STORAGE=", s.FINANCIACION_EDUCATIVA_PRIVATE_STORAGE_BACKEND)
print("PRIVATE_ROOT_EXISTS=", s.FINANCIACION_EDUCATIVA_PRIVATE_ROOT.is_dir())
print("PRIVATE_ROOT_WRITABLE=", os.access(s.FINANCIACION_EDUCATIVA_PRIVATE_ROOT, os.W_OK))
print("RECEIVER_VERSIONS=", ReceptorBrebEducativo.objects.count())
r = receptor_vigente()
print("CURRENT_RECEIVER_ID=", str(r.pk) if r else None)
print("CURRENT_QR_SAFE_AND_INTEGRITY_OK=", r is not None)
archivos = list(ArchivoRecaudoEducativo.objects.order_by("-creado_en").prefetch_related("escaneos")[:100])
print("SUPPORT_SAMPLE_SIZE_MAX100=", len(archivos))
print("SUPPORT_SAMPLE_SCAN_COUNTS=", {state: sum(a.estado_escaneo == state for a in archivos) for state in ("PENDING", "SAFE", "BLOCKED", "ERROR")})
'
```

Debe indicar el ambiente esperado, PostgreSQL, backend real, test backends false,
private root valido y permisos. El diagnostico no imprime nombres, documentos,
referencias bancarias, llave, cuenta completa ni rutas de archivos privados.
No prueba accesibilidad de ClamAV: se acredita mediante un escaneo real autorizado
posteriormente, nunca inventando SAFE. El conteo es una muestra acotada a los
ultimos 100 archivos, no un total global ni una alerta completa; no repetirlo en polling.

## Configurar y certificar en staging

1. Desplegar solo el release revisado/autorizado con canal false; respaldo y
   restauracion de PostgreSQL y private storage comprobados. Este ajuste no crea
   migraciones nuevas. Requiere las ya existentes 0029 de financiacion_educativa
   y 0003 de instituciones; no revertirlas ni aplicarlas de nuevo a ciegas.
2. Si el plan tiene pendientes, revisarlo y aplicar solo con autorizacion
   operacional; no habilitar la web con columnas faltantes. Recolectar estaticos
   con el wrapper correcto y comprobar `check`. No ejecutar ahora por este documento.
3. Asignar permisos al configurador, conciliador y responsable de escaneo.
4. Crear en Admin una nueva version del receptor con datos oficiales verificados
   y el archivo original del banco. No editar la anterior ni cargar un QR de prueba
   como productivo. En staging usar datos sinteticos para probar el recorrido
   completo antes de habilitar una transferencia real expresamente autorizada.
5. Obtener el UUID de `qr` desde el registro readonly de la version. Con el canal
   todavia false, el escaneo acotado con permiso y motivo esta permitido:

   ```bash
   read -r -p 'UUID de archivo QR registrado: ' QR_ARCHIVO_ID
   read -r -p 'ID del operador con permiso de escaneo: ' SCAN_ACTOR_ID
   "$MANAGE" procesar_comprobantes_educativos --once \
     --archivo-id "$QR_ARCHIVO_ID" --actor-id "$SCAN_ACTOR_ID" \
     --motivo 'Verificacion previa autorizada del QR BRE-B'
   ```

   Exigir CLEAN y hash coherente. ERROR/INFECTED no permiten activar. Crear QR
   nuevo si el contenido esta bloqueado; el antivirus no lo convierte en oficial.
6. Tras cotejar titular, banco, cuenta, llave y QR, seleccionar UNA version en
   Admin y usar Activar version seleccionada. Queda evento actor/fecha/motivo.
7. Verificar unidad existente `fundetec-staging-receipts-scan.service` y su
   plantilla versionada, acceso al socket y private root compartido. El comando
   debe ser `manage.py procesar_comprobantes_educativos` sin `--once`.
   `systemd-analyze verify` debe validarse en el VPS; no es una prueba de permisos.
   Con flag false el scanner continuo debe permanecer detenido: el comando
   falla de forma cerrada y Restart=on-failure provocaria un bucle de reinicios.
   El escaneo acotado del QR con actor/motivo es la excepcion previa permitida.
8. Cambiar solo `FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=true` en el entorno
   correcto. Validar sintaxis del env con `bash -n` y ejecutar `check`; reiniciar
   web/scanner para que ambos carguen la misma configuracion:

   ```bash
   sudo systemd-analyze verify /etc/systemd/system/fundetec-staging-receipts-scan.service
   sudo systemctl restart fundetec-staging.service
   sudo systemctl enable --now fundetec-staging-receipts-scan.service
   sudo systemctl restart fundetec-staging-receipts-scan.service
   systemctl is-active fundetec-staging.service fundetec-staging-receipts-scan.service
   staging_manage check
   ```

9. Comprobar health y repetir diagnostico. Entrar como propietario de obligacion
   elegible, desde iPhone/Safari y Android/Chrome: finanzas -> pagos, llave/QR,
   carga JPG/PNG/PDF, estado pendiente, escaneo, evidencia bancaria propia SAFE,
   referencia verificada y confirmacion unica. Probar error/reintento sin perder
   valores, rotacion del QR con 409/recarga, acceso ajeno y replay sin duplicados.
   Verificar APPROVED/autorizacion/financial_terms intactos y ningun saldo ficticio.

### Cinco concurrencias PostgreSQL pendientes

No validadas en esta entrega. Ejecutar despues, con autorizacion, desde el codigo
de staging contra PostgreSQL **de pruebas aislado**, nunca con staging.env ni con
la BD operativa. Crear previamente base y usuario exclusivos de validacion; el
usuario debe poder crear/destruir su BD de test y no tener acceso a datos reales.
No reutilizar resultados historicos. La clase completa ejecuta exactamente:

- test_dos_confirmaciones_mismo_reporte_crean_una_conciliacion
- test_dos_reportes_compiten_por_un_movimiento_global
- test_dos_escaneos_crean_un_solo_intento_limpio
- test_observacion_vacia_concurrente_reutiliza_un_ingreso
- test_dos_reportes_reutilizan_evidencia_con_referencias_distintas

En subshell Bash, usar el Python verificado del ambiente de pruebas, sin imprimir
credenciales. El nombre de base dedicado debe comenzar por `breb_validation_`.
El runner crea/destruye su base `test_...`; no usar --keepdb ni staging_manage.

```bash
(
set -euo pipefail
read -rs -p 'DATABASE_URL de PostgreSQL aislado de pruebas: ' DATABASE_URL
printf '\n'
export DATABASE_URL DJANGO_LOAD_DOTENV=false USE_SQLITE=false DEPLOYMENT_ENVIRONMENT=test
export SECRET_KEY=breb-isolated-tests-only
export FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=false FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED=false
export FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED=false EMAIL_QA_MODE=false EMAIL_LIVE_DELIVERY_ENABLED=false
export OPENAI_API_KEY='' FINANCIACION_EDUCATIVA_ZAPSIGN_API_TOKEN=''
"$VENV_PYTHON" manage.py shell -c 'from django.conf import settings as s; d=s.DATABASES["default"]; assert d["ENGINE"] == "django.db.backends.postgresql"; assert d["NAME"].startswith("breb_validation_"); assert not d.get("TEST", {}).get("NAME"); print("BASE_AISLADA_CONFIGURADA=OK")'
"$VENV_PYTHON" manage.py test financiacion_educativa.tests.test_recaudos_breb.ConcurrenciaRecaudosTests --noinput -v 2
)
```

Exigir cinco aprobadas y ninguna omitida. Registrar version PostgreSQL, SHA,
duracion y resultado. Eliminar despues la base/usuario temporales por el mecanismo
del administrador de pruebas, sin tocar bases operativas. Esto sigue pendiente;
no se ejecuto en staging durante este cierre.

## Activar produccion despues de certificar staging

Usar exclusivamente los valores productivos verificados, no copiar receptor,
usuarios, pruebas, referencias o archivos de staging. Con el canal false repetir
diagnostico, permisos, receptor oficial, escaneo acotado y activacion en Admin.
Exigir aprobacion explicita del responsable del dinero y de operacion.

Tras editar SOLO la flag productiva y validar su archivo:

```bash
bash -n "$ENV_FILE"
production_manage check
systemctl show "$SCAN_UNIT" -p ExecStart --value | grep -q 'procesar_comprobantes_educativos'
! systemctl show "$SCAN_UNIT" -p ExecStart --value | grep -q -- '--once'
sudo systemctl restart "$WEB_UNIT"
sudo systemctl enable --now "$SCAN_UNIT"
sudo systemctl restart "$SCAN_UNIT"
systemctl is-active "$WEB_UNIT" "$SCAN_UNIT"
production_manage check
read -r -p 'URL HTTPS exacta del health de este ambiente: ' HEALTH_URL
curl --fail --silent --show-error --max-time 15 --output /dev/null --write-out 'HEALTH_HTTP=%{http_code}\n' "$HEALTH_URL"
sudo journalctl -u "$SCAN_UNIT" --since '10 minutes ago' --no-pager
```

Ejecutar como bloque con detencion ante error. No continuar por un `active` si
health, entorno o scanner fallan. El journal del comando usa UUID, estado y codigo,
no bytes/documentos; revisar acceso al log antes de compartirlo.
La primera transferencia real requiere autorizacion expresa, identidad bancaria
inequivoca y conciliacion responsable; no se ha realizado ni queda autorizada
por las pruebas sinteticas de este repositorio.

## Incidentes y reversion

Desactivar la flag del ambiente, reiniciar web y detener/deshabilitar solo su
scanner. Si el receptor no es fiable, deshabilitarlo en Admin para registrar
el evento; esto no exige que el archivo siga accesible. No borrar datos ni
revertir 0029/0003 para desactivar el canal. Un cambio de QR requiere nueva
version, escaneo y activacion; no modificar archivos existentes en disco.

Reintento tecnico: mismo comando acotado --once/--archivo-id/--actor-id/--motivo
despues de resolver la causa. Leases/backoff y limites existentes se conservan.
Un resultado infectado no se vuelve limpio por accion manual. Una conciliacion
equivocada no se edita ni se borra: conservarla y escalar; las compensaciones
requieren el siguiente bloque financiero aprobado. No aplicar ajustes por SQL.
