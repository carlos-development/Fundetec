# Recaudos educativos BRE-B: Fase 2A

## Alcance e identidad bancaria

Reporte, comprobante privado, escaneo, conciliacion manual y cifras de ingresos.
No aplica capital/intereses, liquida pago total, cambia fechas contractuales,
causa mora ni emite paz y salvo. Las solicitudes siguen APPROVED y su API
conserva autorizacion y financial_terms. No hay correos nuevos, cambios al
outbox ni destinatarios institucionales adicionales en esta fase.

El movimiento verificado se identifica globalmente por **cuenta receptora +
referencia bancaria unica**. No por tenant, monto, fecha o hash del recibo.
La cuenta usa identificadores bancarios estables, ASCII en mayusculas y sin
espacios/guiones de formato; no crear aliases de un mismo banco/cuenta.
Las referencias se normalizan a mayusculas, conservando ceros y puntuacion
significativa; no se admiten espacios. El representante legal debe usar el
identificador unico del extracto bancario, no una referencia que se repita.
Si el banco no suministra una identidad inequivoca, no confirmar por conjetura.
La referencia declarada por el pagador nunca reserva un movimiento.

Cuenta, receptor versionado, QR, reportes, rechazos y conciliaciones conservan
actor/fecha. El reporte almacena la version mostrada y la fotografia firmada.
Un cambio del receptor no modifica historicos; un formulario de version antigua
debe recargarse y verificarse con soporte antes de reportar.
Visualizacion y descarga del QR incluyen el UUID del receptor mostrado. Si esa
version dejo de estar disponible, responden 409 y exigen recargar; nunca
sustituyen el archivo por el QR de otra version o cuenta. Un UUID invalido o
el acceso a una obligacion ajena no permite consultar el QR.
No hay edicion, borrado ni reversion de conciliaciones: resolver estos casos
requiere un incremento compensatorio autorizado. No usar SQL directo para
corregirlos. La proteccion de inmutabilidad cubre el ORM, no SQL directo.

## Propiedad y datos

Rutas del pagador bajo `solicitudes/<uuid>/pagos/`: cuenta autenticada activa,
asociacion existente a solicitud.usuario y comprobacion de propiedad ya usada
por el flujo educativo. No se concede acceso por parentesco, correo enviado,
documento o UUID. Para menores se conserva el acceso previamente asociado;
no se agrega acceso independiente a tutores que no lo tengan.

El QR adjunto real no se incorpora al repositorio ni a fixtures. El operador
autorizado debe cargar y verificar el QR oficial y la llave posteriormente.
No se generan QR financieros. JPG/PNG se muestran; PDF se abre/descarga como
PDF sin conversion. Copiar llave funciona con Clipboard o seleccion manual.
No se garantiza lectura desde galeria por aplicaciones bancarias.

JPG/PNG/PDF validos (Pillow/PdfReader), hasta 10 MiB y 20 paginas PDF, sin PDF
cifrado. Soportes almacenados en storage privado, UUID sin nombre original,
hash/tamano y finalidad QR/recibo/evidencia bancaria. No se envian a IA.
Lectura solo SAFE, con no-store, nosniff, no-referrer y CSP sandbox.
El escaneo utiliza el adaptador ClamAV existente; no basta el MIME declarado.

## Configuracion y permisos

Inicialmente:

```dotenv
FINANCIACION_EDUCATIVA_RECAUDOS_ENABLED=false
FINANCIACION_EDUCATIVA_RECAUDO_MAX_BYTES=10485760
```

Reutiliza FINANCIACION_EDUCATIVA_PRIVATE_ROOT, PRIVATE_STORAGE_BACKEND,
DOCUMENT_SCAN_BACKEND, CLAMAV_* y SCAN_MAX_ATTEMPTS/SCAN_STALE_SECONDS existentes.
No necesita credenciales bancarias, OpenAI ni ePayco.

Cuando Carlos identifique al responsable, asignar desde grupos/permisos en
Django Admin a su cuenta individual (no crear cuentas de ejemplo):

- Consulta: acceder_dashboard_operativo + consultar_recaudos_educativos.
- Representante legal: lo anterior + conciliar_recaudos_educativos.
- Configuracion de receptor: configurar_receptor_breb y permisos de consulta
  del modelo ReceptorBrebEducativo para ver/seleccionar sus versiones en Admin.
- Recuperacion de antivirus: escanear_comprobantes_educativos.

Desde Admin crear una version del receptor (banco/cuenta estables, titular,
llave y QR). Todavia no habilita pagos. Escanear el QR con actor/motivo:

```bash
staging_manage procesar_comprobantes_educativos --once --archivo-id UUID_QR --actor-id ID_OPERADOR --motivo 'Verificacion previa del QR oficial'
```

Solo tras SAFE, seleccionar una version en Admin y usar Activar. La activacion
o deshabilitacion conserva un evento con actor/motivo; la ultima determina el
receptor vigente global de APROBADO. La version es inmutable: cambiar datos
requiere otra version, no editar la anterior.

## Secuencia propuesta, no ejecutada

1. Respaldo habitual PostgreSQL y archivos privados; desplegar release revisado
   con flag false. Instalar dependencias existentes, sin nuevas dependencias.
2. Revisar `staging_manage migrate --plan`, aplicar 0029_recaudos_breb (depende
   de 0028), collectstatic y check. No transforma historicos ni crea recaudos.
3. Crear receptor/permisos, escanear QR y activarlo como se indica arriba.
4. Copiar `deploy/systemd/fundetec-staging-receipts-scan.service` a
   `/etc/systemd/system/`; revisar `systemd-analyze verify`, daemon-reload y
   dejar disabled/stopped. Usa el venv compartido y solo shared/private writable.
5. Activar RECAUDOS_ENABLED=true, reiniciar web y arrancar/habilitar la unidad.
   Certificar reporte/confirmacion sinteticos en staging y movil antes de uso real.
6. Rollback: desactivar flag, detener/disable scanner y volver al codigo
   anterior compatible. No borrar evidencia ni revertir la migracion con datos.

```bash
sudo cp deploy/systemd/fundetec-staging-receipts-scan.service /etc/systemd/system/
sudo systemd-analyze verify /etc/systemd/system/fundetec-staging-receipts-scan.service
sudo systemctl daemon-reload
sudo systemctl disable --now fundetec-staging-receipts-scan.service
# Solo despues de configurar, activar y autorizar:
sudo systemctl enable --now fundetec-staging-receipts-scan.service
sudo systemctl status fundetec-staging-receipts-scan.service --no-pager
sudo journalctl -u fundetec-staging-receipts-scan.service --since '10 minutes ago' --no-pager
```

## Operacion y recuperacion

La bandeja `/financiacion-educativa/operaciones/recaudos/` separa reportados
pendientes por creacion e ingresos confirmados por fecha efectiva del banco.
La evidencia bancaria independiente se carga desde el detalle del reporte,
se escanea y su UUID se selecciona para confirmar; no basta el recibo del pagador.
Cuenta, referencia verificada, monto, fecha, evidencia y observacion obligatorios.
Si difiere el monto, es obligatoria la justificacion separada y se conservan ambos.
Rechazo requiere motivo publico/siguiente paso y no rechaza la financiacion.
El ingreso confirmado se cuenta una vez, sin reducir capital ni saldo de deuda.
No disponible: capital pagado, intereses recaudados, saldo real y mora efectiva.

Scanner continuo sin --once, responde SIGTERM, serializa por archivo. Errores
cierran la lectura y la confirmacion; backoff 30..600 s, hasta el maximo existente.
Un STARTED abandonado se registra ERROR y se recupera despues de SCAN_STALE_SECONDS.
Al agotarse, queda ERROR con codigo operativo; reintento manual limitado a un UUID
con permiso, actor y motivo (no alterar datos ni inventar SAFE):

```bash
staging_manage procesar_comprobantes_educativos --once --archivo-id UUID_SOPORTE --actor-id ID_OPERADOR --motivo 'Fallo transitorio resuelto; reintento autorizado'
```

Un INFECTED no se reabre: nuevo reporte/soporte seguro, manteniendo el anterior.
Diagnostico sin PII: UUID de archivo y escaneos numero/estado/codigo_error/fechas.

## Proximos bloques

2B: aplicaciones, saldos/cortes y cuotas/abonos sobre ingresos conciliados.
COP, 1% mensual, causacion diaria comercial 30; abonos con intereses a fecha,
reduccion de plazo conservando cuota; no desplazar por festivos. Especificar
pagos tardios/parciales, excedentes, retroactividad, correcciones y cierre antes
de automatizar. Las proyecciones hipoteticas no son pagos efectivos.

2C: ePayco requiere comercio y credenciales sandbox autorizados; confirmar
servidor a servidor autenticidad, orden, importe/moneda, ambiente, repeticion y
reversos. No hay adaptador ni instalacion en esta fase. Referencias facilitadas
en el alcance: documentacion oficial de BRE-B y ePayco; certificacion futura
requiere consultar su contrato vigente, sin asumir equivalencia con BRE-B.

## Validacion local del 2026-10-06

Base de partida: main, 57607ccc32c3e057a0fe740c93403ba63ead2de9.
PostgreSQL 16.13 temporal en Docker, accesible solo por loopback; sin cargar
`.env`, credenciales del servidor, documentos reales ni proveedores externos.
La ejecucion previa al cierre de URLs del QR termino con **136 pruebas OK en 256.552 segundos, cero
omisiones**, sobre estos modulos:

```text
financiacion_educativa.tests.test_recaudos_breb
financiacion_educativa.tests.test_recaudos_breb_browser
financiacion_educativa.tests.test_dashboard_obligaciones
financiacion_educativa.tests.test_dashboard_operativo
financiacion_educativa.tests.test_api_institucional
financiacion_educativa.tests.test_firma_educativa
financiacion_educativa.tests.test_resultado_sandbox
financiacion_educativa.tests.test_outbox_correos
```

Pasaron las tres concurrencias de recaudos: mismo reporte, mismo movimiento
bancario para dos reportes y escaneo del mismo archivo. Tambien se ejecutaron
las concurrencias existentes de Sandbox y outbox, sin omitirlas por backend.
La base de pruebas fue destruida por Django, quedaron cero conexiones a ella
y se elimino el contenedor temporal junto con sus datos.

Navegador: Edge headless con Playwright local, 375x667 y 390x844; QR/llave,
formulario, subida, estados, bandeja y detalle sin overflow. El envio nativo
sin JavaScript paso con preferencia de movimiento reducido, evitando que el
scroll suave desplace el toque automatizado. No sustituye la certificacion en
Safari/iPhone y Chrome/Android reales ni la prueba de ClamAV en staging.

`manage.py check`: cero issues. `makemigrations --check --dry-run`: sin cambios.
`migrate financiacion_educativa --plan` sobre la base temporal ya migrada:
sin operaciones pendientes. SQL de 0029 revisado: ocho tablas nuevas,
restricciones e indices; sin modificaciones de datos historicos.
`git diff --check` y ambos `node --check`: OK.
`systemd-analyze` no esta disponible en Windows: se valido la plantilla con
la prueba focalizada, pero su verificacion nativa sigue obligatoria antes de
instalarla en staging. No se ejecutaron despliegues ni migraciones en servidores.

### Cierre de URLs del QR

Visualizacion y descarga resuelven exclusivamente el `receptor_id` incluido
en la pagina, conservan propiedad y SAFE, y exigen recargar con respuesta 409
si esa version ya no esta disponible. No hay sustitucion silenciosa del QR.
Se agregaron siete pruebas: enlaces/archivo de la version mostrada, rotacion
con la misma cuenta, cambio de cuenta, UUID invalido o ausente, desactivacion,
acceso ajeno y bloqueo de lectura cuando el escaneo no es SAFE.

Comando focalizado:

```text
python manage.py test financiacion_educativa.tests.test_recaudos_breb.RecaudosBrebTests --noinput --verbosity 2
```

Resultado en SQLite en memoria: **38 pruebas OK en 68.289 segundos, cero
omisiones**. La base se destruyo al terminar. No se repitieron las 136 pruebas
ni PostgreSQL: este ajuste no modifica modelos, migraciones o bloqueos; las
tres pruebas concurrentes conservan la validacion PostgreSQL anterior.
Check, deteccion de migraciones, diff --check y node --check: OK.

## Archivos del incremento

Modificados (14):

```text
.env.example
aprobado_web/settings.py
docs/ROADMAP_FINANCIACION_EDUCATIVA.md
docs/gobernanza_dashboard_operativo.md
financiacion_educativa/admin.py
financiacion_educativa/dashboards/operaciones/permissions.py
financiacion_educativa/dashboards/operaciones/urls.py
financiacion_educativa/models.py
financiacion_educativa/services/limpieza_solicitudes.py
financiacion_educativa/web/urls.py
financiacion_educativa/web/views.py
templates/financiacion_educativa/dashboards/operaciones/_pagos_no_disponibles.html
templates/financiacion_educativa/dashboards/operaciones/_sidebar.html
templates/financiacion_educativa/estado_solicitud.html
```

La limpieza QA solo reconoce las dependencias nuevas como preservadas: no
elimina reportes, conciliaciones ni rechazos. Evita romper la inspeccion de
dependencias de las solicitudes anteriores y conserva su evidencia.

Nuevos (19):

```text
deploy/systemd/fundetec-staging-receipts-scan.service
docs/RECAUDOS_BREB_FASE_2A.md
financiacion_educativa/admin_recaudos.py
financiacion_educativa/management/commands/procesar_comprobantes_educativos.py
financiacion_educativa/migrations/0029_recaudos_breb.py
financiacion_educativa/models_recaudos.py
financiacion_educativa/services/recaudos.py
financiacion_educativa/templatetags/recaudo_format.py
financiacion_educativa/tests/js/test_recaudos_breb_browser.js
financiacion_educativa/tests/test_recaudos_breb.py
financiacion_educativa/tests/test_recaudos_breb_browser.py
financiacion_educativa/web/forms_recaudos.py
financiacion_educativa/web/recaudos.py
static/css/financiacion_educativa_recaudos.css
static/js/financiacion_educativa_recaudos.js
templates/financiacion_educativa/recaudos/bandeja.html
templates/financiacion_educativa/recaudos/detalle_operativo.html
templates/financiacion_educativa/recaudos/estado.html
templates/financiacion_educativa/recaudos/reportar.html
```
