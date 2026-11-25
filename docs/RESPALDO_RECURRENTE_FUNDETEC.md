# Respaldo recurrente de Fundetec

El servicio `fundetec-backup@.service` genera un paquete local para cada instancia, `staging` o `production`. El temporizador se renderiza después de aprobar la ventana de mantenimiento. La propuesta de frecuencia es diaria. La producción queda pendiente de aprobar la ventana, la retención y el destino externo. El procedimiento de consistencia, custodia y recuperación sigue vigente. [Procedimiento de respaldo y restauración](RESPALDO_RESTAURACION_ALERTAS_EDUCATIVAS.md)

## Preparar la instancia

1. Inventariar la base de PostgreSQL, la raíz privada que usa la aplicación y cualquier otro almacenamiento que deba quedar cubierto. Configurar un rol PostgreSQL de respaldo con lectura sobre toda la base y permisos suficientes para `pg_dump`. Usar un socket local y herramientas de la misma versión mayor que el servidor. Prever espacio para el volcado y el tar en el directorio temporal privado del servicio, además del resultado cifrado en `/var/lib/fundetec-backup-<entorno>`.
2. Crear `/etc/fundetec/backup-<entorno>.env` con los valores aprobados. Incluir en `BACKUP_WRITER_UNITS` web, worker educativo, outbox, escáner, recordatorios y sus temporizadores, y las demás unidades que escriban en la base o los archivos. Infraestructura debe suspender también cargas, tareas manuales y escritores fuera de systemd durante la ventana.

   | Variable | Valor |
   |---|---|
   | `BACKUP_PRIVATE_ROOT` | Ruta absoluta de la raíz privada efectiva |
   | `BACKUP_PG_SERVICE` | Nombre de la sección libpq del entorno |
   | `BACKUP_GPG_FINGERPRINT` | Huella completa del destinatario de cifrado aprobado |
   | `BACKUP_WRITER_UNITS` | Nombres de unidades escritoras separados por espacios |

3. Proveer `backup-<entorno>.pg_service.conf`, `backup-<entorno>.pgpass` y `backup-<entorno>-recipient.asc` en `/etc/fundetec/` para `LoadCredential`. Mantener el passfile restringido y la clave privada de descifrado bajo custodia independiente. El paquete contiene datos, el SHA del checkout y la versión de PostgreSQL. La configuración de la aplicación y las claves se custodian por separado.
4. En la ventana aprobada, detener los escritores por el procedimiento operativo. El servicio comprueba las unidades configuradas antes y después de capturar y aborta si están activas o cambiaron de estado. El propio respaldo no detiene ni reinicia servicios. Renderizar `@@CALENDARIO_APROBADO@@` con la expresión y zona horaria aprobadas. Verificar la instancia con `systemd-analyze verify` y la expresión con `systemd-analyze calendar` antes de habilitar. El temporizador usa `Persistent=false` para evitar ejecuciones de recuperación fuera de la ventana.
5. Verificar la salida del servicio y cotejar el SHA-256 del archivo cifrado. Copiar únicamente el paquete cifrado y el checksum al destino externo aprobado. Cotejar el objeto en destino y aplicar la política de retención autorizada sobre copias confirmadas.

## Restaurar en aislamiento

1. Verificar el SHA-256 externo antes de descifrar. Usar la clave bajo custodia en un directorio temporal con permisos `0700`. Extraer en un directorio nuevo, comprobando que el tar solo contenga archivos normales y directorios contenidos en el destino. Rechazar rutas absolutas, traversal, enlaces y entradas especiales.
2. Cotejar `SHA256SUMS.json` contra `database.dump`, `metadata.json` y todos los archivos en `private/`. Comprobar que el conjunto extraído coincide con el del manifiesto. Usar una base nueva y aislada, de versión compatible, y `pg_restore --exit-on-error --no-owner --no-acl`. Registrar el esquema de `django_migrations` antes de aplicar cualquier migración.
3. Arrancar la aplicación del SHA registrado con DSN y raíz privada nuevos en loopback, sin entregas externas. Consultar `/health/`, cotejar filas de muestra y abrir archivos privados comparando sus hashes y el acceso de un titular con el de una cuenta ajena. Registrar el tiempo desde el inicio del respaldo hasta la última comprobación. Eliminar el material temporal según el procedimiento de custodia.
