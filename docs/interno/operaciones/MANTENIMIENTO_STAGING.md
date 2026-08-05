# Mantenimiento de staging

Procedimiento interno para `staging-api.aprobado.com.co`. Todos los comandos
de Django deben cargar exclusivamente el entorno de staging y ejecutarse como
`fundetec-staging`.

## Rutas autorizadas

| Recurso | Ruta |
| --- | --- |
| Codigo | `/var/www/fundetec-staging/current` |
| Variables | `/var/www/fundetec-staging/shared/staging.env` |
| Virtualenv | `/var/www/fundetec-staging/shared/venv` |
| Servicio | `fundetec-staging.service` |

Confirmar el archivo cargado por systemd:

```bash
systemctl show fundetec-staging.service -p EnvironmentFiles
```

La aplicacion no carga `current/.env` ni `shared/.env`. No deben crearse ni
usarse como sustitutos de `staging.env`.

## Ejecutar comandos Django

Definir esta funcion en la sesion administrativa. No imprime el entorno:

```bash
staging_manage() {
  runuser -u fundetec-staging -- env -i \
    HOME=/tmp \
    PATH=/var/www/fundetec-staging/shared/venv/bin:/usr/bin:/bin \
    LANG=C.UTF-8 \
    bash -c '
      set -Eeuo pipefail
      set -a
      . /var/www/fundetec-staging/shared/staging.env
      set +a
      export PYTHONDONTWRITEBYTECODE=1
      cd /var/www/fundetec-staging/current
      exec /var/www/fundetec-staging/shared/venv/bin/python manage.py "$@"
    ' bash "$@"
}
```

Comprobacion:

```bash
staging_manage check
```

Ejecutar `enviar_correos_prueba_educacion` sin `manage.py` ni el interprete
produce `command not found`; es un comando Django, no un ejecutable del sistema.

## Cambiar variables

Crear primero un respaldo exclusivo de root, fuera del arbol compartido:

```bash
install -d -o root -g root -m 0700 \
  /var/backups/fundetec-staging/config

install -o root -g root -m 0600 \
  /var/www/fundetec-staging/shared/staging.env \
  "/var/backups/fundetec-staging/config/staging.env.$(date +%Y%m%d-%H%M%S)"
```

Editar solo el archivo real:

```bash
nano /var/www/fundetec-staging/shared/staging.env
chown root:fundetec-staging \
  /var/www/fundetec-staging/shared/staging.env
chmod 0640 /var/www/fundetec-staging/shared/staging.env
staging_manage check
```

No mostrar ni compartir el archivo completo. Contiene contrasenas y claves
HMAC. Variables de correo habituales:

- `EMAIL_HOST_USER`;
- `EMAIL_HOST_PASSWORD`;
- `DEFAULT_FROM_EMAIL`;
- `SERVER_EMAIL`;
- `CONTACT_EMAIL`;
- `CREDIT_INTERNAL_NOTIFICATION_EMAILS`;
- `EMAIL_QA_REDIRECT_TO`.

Aplicar el cambio solo a staging:

```bash
systemctl restart fundetec-staging.service
systemctl is-active fundetec-staging.service

curl --silent --show-error --output /dev/null \
  --write-out 'HTTP %{http_code}\n' --max-time 15 \
  https://staging-api.aprobado.com.co/health/

journalctl -u fundetec-staging.service \
  --since '5 minutes ago' --priority=0..3 --no-pager
```

No es necesario reiniciar Nginx ni PostgreSQL por un cambio en
`staging.env`.

## Desplegar un commit aprobado sin migraciones

No ejecutar Git como root. Sustituir `COMMIT_APROBADO` por un hash completo ya
publicado en `origin`:

```bash
cd /var/www/fundetec-staging/current

runuser -u fundetec-staging -- git status --short
runuser -u fundetec-staging -- git fetch origin
runuser -u fundetec-staging -- git cat-file -e 'COMMIT_APROBADO^{commit}'
runuser -u fundetec-staging -- git checkout --detach COMMIT_APROBADO
runuser -u fundetec-staging -- git status --short

staging_manage check
staging_manage showmigrations
systemctl restart fundetec-staging.service
systemctl is-active fundetec-staging.service

curl --silent --show-error --output /dev/null \
  --write-out 'HTTP %{http_code}\n' --max-time 15 \
  https://staging-api.aprobado.com.co/health/
```

Si `showmigrations` informa operaciones pendientes, detenerse y aplicar el
procedimiento de migracion aprobado antes de reiniciar. Para un cambio sin
migraciones, la reversión consiste en volver al hash anterior aprobado como
`fundetec-staging` y reiniciar únicamente este servicio.

## Probar correo

Mantener `EMAIL_QA_MODE=true` para que todos los mensajes se desvien al unico
destinatario QA configurado.

```bash
staging_manage enviar_correos_prueba_educacion --help
staging_manage enviar_correos_prueba_educacion \
  --destinatario medio.datain@gmail.com --confirmar
```

El segundo comando envia nueve muestras inertes por SMTP.

## Credenciales institucionales

La autenticacion usa:

```text
Authorization: ApiKey <prefijo>.<secreto>
```

El prefijo y el hash del secreto se guardan en
`CredencialAPIInstitucion`. El secreto en claro solo existe durante emision o
rotacion. `APROBADO_INSTITUTION_API_KEY` es una variable heredada y no crea
credenciales para esta API.

Los valores de `alcances` son metadatos: los permisos actuales autentican la
credencial y la institucion, pero todavia no aplican autorizacion por alcance.

### Consultar instituciones y credenciales

```bash
staging_manage listar_instituciones_api --solo-activas
staging_manage listar_credenciales_institucionales
staging_manage listar_credenciales_institucionales \
  --institucion-id UUID_INSTITUCION
```

Los listados no consultan ni muestran hashes o secretos.

### Entrega segura del token

La opcion recomendada escribe un archivo nuevo `0600` y no muestra el token en
stdout. Preparar un directorio temporal exclusivo:

```bash
install -d -o fundetec-staging -g fundetec-staging -m 0700 \
  /var/www/fundetec-staging/shared/private/credential-delivery
```

Emitir una credencial:

```bash
staging_manage emitir_credencial_institucional \
  --institucion-id UUID_INSTITUCION \
  --nombre 'NOMBRE OPERATIVO UNICO' \
  --prefijo 'fundetec_ingles' \
  --expira-en '2027-01-31T23:59:59-05:00' \
  --archivo-token \
  /var/www/fundetec-staging/shared/private/credential-delivery/credencial.token
```

El archivo no se sobrescribe. Debe importarse inmediatamente en un gestor de
secretos con control de acceso y luego eliminarse:

```bash
shred -u \
  /var/www/fundetec-staging/shared/private/credential-delivery/credencial.token
```

Como alternativa excepcional, `--mostrar-token` lo imprime una sola vez. No
capturarlo en variables del shell ni guardarlo en el historial.

`--prefijo` es opcional. Se eliminan espacios en los extremos, se convierte a
minusculas y se aceptan unicamente letras ASCII, numeros, guion y guion bajo,
con un maximo de 16 caracteres. Debe ser unico globalmente. Si se omite, se
mantiene la generacion aleatoria automatica.

El prefijo de una credencial emitida es inmutable. La rotacion cambia solamente
el secreto y conserva el prefijo. Para cambiarlo, emitir una credencial nueva,
validar su uso y revocar despues la credencial anterior.

### Rotar y revocar

La rotacion invalida inmediatamente el secreto anterior. Si no se indica una
fecha, conserva la expiracion vigente; `--sin-expiracion` la elimina de forma
explicita.

```bash
staging_manage rotar_credencial_institucional \
  --credencial-id UUID_CREDENCIAL \
  --confirmar \
  --archivo-token \
  /var/www/fundetec-staging/shared/private/credential-delivery/rotada.token

staging_manage revocar_credencial_institucional \
  --credencial-id UUID_CREDENCIAL \
  --confirmar
```

La revocacion es idempotente y conserva la trazabilidad. Ninguna operacion
requiere editar PostgreSQL directamente.

## Escaneo antivirus documental

El dominio educativo usa un puerto interno y el adaptador ClamAV configurado
por entorno. Debe configurarse exactamente un destino: socket Unix, o bien TCP
dejando `FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET` vacio y configurando host y
puerto. `manage.py check` rechaza configuraciones vacias, ambiguas o invalidas.

Variables operativas:

- `FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND`;
- `FINANCIACION_EDUCATIVA_CLAMAV_UNIX_SOCKET`;
- `FINANCIACION_EDUCATIVA_CLAMAV_HOST`;
- `FINANCIACION_EDUCATIVA_CLAMAV_PORT`;
- `FINANCIACION_EDUCATIVA_CLAMAV_CONNECT_TIMEOUT_SECONDS`;
- `FINANCIACION_EDUCATIVA_CLAMAV_READ_TIMEOUT_SECONDS`;
- `FINANCIACION_EDUCATIVA_SCAN_MAX_ATTEMPTS`;
- `FINANCIACION_EDUCATIVA_SCAN_STALE_SECONDS`;
- `FINANCIACION_EDUCATIVA_SCAN_MAX_REOPENINGS`;
- `FINANCIACION_EDUCATIVA_SCAN_REOPEN_EXTRA_ATTEMPTS`.

Los checks de Django reportan identificadores `financiacion_educativa.E005` a
`E011` cuando los valores ya cargados tienen tipos o rangos invalidos. Las
variables se convierten con `int()` o `float()` durante la importacion de
`settings.py`; por ello, texto no convertible en `staging.env` detiene Django
con `ValueError` antes de que `manage.py check` pueda emitir esos
identificadores. En ese caso debe corregirse la variable y volver a ejecutar
el check, sin intentar iniciar el servicio.

Comprobar el daemon sin exponer archivos privados y procesar pendientes:

```bash
systemctl is-active clamav-daemon.service
staging_manage procesar_escaneos_documentales --help
staging_manage procesar_escaneos_documentales --limit 50
staging_manage procesar_escaneos_documentales \
  --solicitud-id UUID_SOLICITUD --limit 20
staging_manage procesar_escaneos_documentales \
  --documento-id UUID_DOCUMENTO
```

Los errores operativos conservan el documento pendiente y generan un intento
auditable. Solo un veredicto limpio del adaptador cambia el estado a `SAFE`.
El administrador puede solicitar el mismo procesamiento, pero no asignar ese
estado manualmente.

El manager predeterminado de `DocumentoFinanciacion` bloquea `update()`,
`bulk_update()` y altas inseguras por `bulk_create()` sobre los campos de
estado, puntero y vigencia del escaneo. Esta proteccion cubre operaciones del
ORM de Django; no sustituye los controles de acceso a PostgreSQL ni protege
frente a SQL directo. El SQL directo no forma parte del procedimiento
operativo.

Cuando se agota el presupuesto, una reapertura exige un actor con el permiso
`escanear_documento_financiacion` y un motivo operativo. La operacion conserva
los intentos anteriores y agrega solamente el presupuesto configurado:

```bash
staging_manage reabrir_escaneo_documental \
  --documento-id UUID_DOCUMENTO \
  --actor-id ID_USUARIO_OPERADOR \
  --motivo 'ClamAV restablecido despues de incidente operativo'
staging_manage procesar_escaneos_documentales --documento-id UUID_DOCUMENTO
```

La concurrencia entre procesos debe validarse sobre PostgreSQL antes del
despliegue de este bloque. Con `staging.env` cargado, ejecutar:

```bash
staging_manage test \
  financiacion_educativa.tests.test_escaneo_concurrencia_postgresql \
  --noinput
```

En SQLite esa prueba se reporta como `SKIPPED`; ese resultado no demuestra la
concurrencia de produccion.

## Orquestacion educativa automatica

El recorrido automatico permanece desactivado hasta completar las pruebas de
ClamAV, IA documental y ZapSign sandbox:

```text
FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=false
```

Antes de cambiarlo a `true`, deben estar configuradas y validadas las variables
de `.env.example` para:

- ClamAV: backend, destino, timeouts y limites de intentos;
- IA: backend OpenAI, modelo, timeout, umbrales y `OPENAI_API_KEY`;
- firma: backend educativo, URL sandbox, token, secreto/header de webhook,
  timeout, intentos, modo de autenticacion y HMAC de destinatario;
- contrato: `FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL`.

`staging_manage check` rechaza activar la orquestacion con IA o firma
deshabilitadas o sin acreedor. La activacion debe probarse primero con una
solicitud QA y correo desviado. El callback posterior al commit se ejecuta en
el proceso web; no es una cola asincrona.

La identificacion capturada, la identificacion adicional del responsable y el
certificado de ingresos requieren validacion visual concluyente. La captura de
identidad solo admite JPEG o PNG. Si uno de los demas documentos cuyo contenido
debe validarse llega en PDF y el backend no soporta ese formato, queda en
revision manual con el motivo persistido; no se convierte ni acepta en silencio.

El soporte de matricula es opcional y sus datos se registran por separado. Un
soporte PDF estructuralmente valido puede aceptarse por politica deterministica
solo despues de un escaneo ClamAV limpio y con los datos de matricula completos;
la decision y su motivo quedan en `resultado_procesamiento` y no se registra
como validacion IA. Las imagenes de matricula siguen requiriendo validacion IA.
Baja confianza, posible imagen no real, inconsistencia o fallo tecnico conserva
la solicitud en revision manual. Ninguno de esos casos produce rechazo
automatico.

Un fallo temporal deja intentos y estados recuperables. Reanudar sin editar la
base ni repetir pasos manuales:

```bash
staging_manage procesar_orquestacion_educativa --help
staging_manage procesar_orquestacion_educativa \
  --solicitud-id UUID_SOLICITUD --limit 1
```

Los comandos de escaneo, IA y firma individuales se conservan para diagnostico
puntual. No son parte obligatoria del recorrido exitoso cuando la automatizacion
esta activa.
