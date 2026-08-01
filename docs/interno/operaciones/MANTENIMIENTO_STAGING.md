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
