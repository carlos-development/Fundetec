# Reanudar Aprobado en desarrollo local

Guia operativa para continuar trabajando despues de reiniciar Windows. No es
necesario clonar nuevamente el repositorio ni recrear el entorno en cada inicio.

## Resumen de frecuencia

| Accion | Instalacion inicial | Cada reinicio | Cuando cambia el repositorio |
| --- | --- | --- | --- |
| Abrir el repositorio | Si | Si | Si |
| Crear `venv` | Si | No | No |
| Activar `venv` | Si | Si | Si |
| Instalar dependencias | Si | No | Si cambia `requirements.txt` |
| Revisar variables | Si | Solo si hay errores | Si cambia configuracion |
| Ejecutar migraciones | Si | No | Si hay migraciones nuevas |
| Crear politica financiera | Si falta | No | Si vence o cambia la politica |
| `manage.py check` | Si | Recomendado | Si |
| Iniciar servidor | Si | Si | Si |

## 1. Abrir la carpeta correcta

PowerShell:

```powershell
Set-Location C:\.vscode\Fundetec
git status --short
```

Git Bash:

```bash
cd /c/.vscode/Fundetec
git status --short
```

El archivo `manage.py` debe existir en la carpeta actual:

```powershell
Test-Path .\manage.py
```

## 2. Activar el entorno virtual

PowerShell:

```powershell
.\venv\Scripts\Activate.ps1
python --version
```

Si PowerShell bloquea el script solo para la sesion actual:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1
```

Git Bash:

```bash
source venv/Scripts/activate
python --version
```

Crear el entorno solo en una instalacion nueva:

```powershell
py -3.12 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 3. Comprobar dependencias

```powershell
.\venv\Scripts\python.exe -m pip check
```

Si `requirements.txt` cambio o falta un paquete:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

No es necesario reinstalar dependencias despues de cada reinicio.

## 4. Revisar configuracion sin mostrar secretos

La configuracion sensible debe permanecer en variables del sistema, proveedor o
archivo local excluido de Git. Nunca pegar secretos en tickets, commits o
capturas.

Comprobacion segura en PowerShell:

```powershell
$names = @(
    'DEBUG',
    'USE_SQLITE',
    'DATABASE_URL',
    'SECRET_KEY',
    'EMAIL_BACKEND',
    'EMAIL_HOST_USER',
    'EMAIL_HOST_PASSWORD',
    'FINANCIACION_EDUCATIVA_PRIVATE_ROOT'
)
$names | ForEach-Object {
    [PSCustomObject]@{
        Variable = $_
        Configurada = [bool](Get-Item "Env:$_" -ErrorAction SilentlyContinue)
    }
}
```

En desarrollo, si `DATABASE_URL` no esta configurada, el proyecto usa SQLite
por el fallback vigente de `settings.py`. No descomentar credenciales locales.

Para evitar correos reales durante pruebas manuales:

```powershell
$env:EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
```

## 5. Aplicar migraciones

Primera instalacion o despues de recibir migraciones nuevas:

```powershell
.\venv\Scripts\python.exe manage.py showmigrations
.\venv\Scripts\python.exe manage.py migrate
```

Git Bash:

```bash
venv/Scripts/python.exe manage.py showmigrations
venv/Scripts/python.exe manage.py migrate
```

No ejecutar `migrate` contra staging o produccion desde un equipo local sin el
procedimiento y respaldo aprobados.

## 6. Configurar la politica financiera educativa

Comprobar si existe una `EDU_STANDARD` activa y aplicable hoy:

```powershell
.\venv\Scripts\python.exe manage.py diagnosticar_politica_financiera_educativa
```

Si el ambiente es nuevo y no existe una politica:

```powershell
.\venv\Scripts\python.exe manage.py configurar_politica_financiera_educativa `
  --vigente-desde 2026-01-01 `
  --policy-version 1 `
  --activate
```

El comando es idempotente. La fecha y version deben corresponder al ambiente y
a la decision financiera aprobada. No se ejecuta en cada reinicio.

La politica no se guarda en cache y no requiere reiniciar `runserver` despues
de activarla. Si el servidor y una terminal muestran resultados distintos,
ejecuta el diagnostico desde la misma terminal y con las mismas variables que
inician `runserver`; compara `DJANGO_SETTINGS_MODULE`, `DATABASE_ENGINE`,
`DATABASE_ID`, `TIME_ZONE` y `LOCAL_DATE`.

Tambien puede revisarse desde:

```text
http://127.0.0.1:8001/admin/financiacion_educativa/configuracionfinancieraeducativa/
```

## 7. Verificar Django

```powershell
.\venv\Scripts\python.exe manage.py check
.\venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

El segundo comando debe responder `No changes detected`.

## 8. Iniciar y detener el servidor

```powershell
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Abrir:

```text
http://127.0.0.1:8001/
http://127.0.0.1:8001/accounts/login/
http://127.0.0.1:8001/api/v1/schema/
http://127.0.0.1:8001/admin/
```

Detener correctamente en la terminal:

```text
Ctrl+C
```

No cerrar a la fuerza mientras se esta ejecutando una migracion o comando de
administracion.

## 9. Diagnostico rapido

### Migraciones pendientes

```powershell
.\venv\Scripts\python.exe manage.py showmigrations financiacion_educativa instituciones
.\venv\Scripts\python.exe manage.py migrate
```

### No existe configuracion financiera vigente

Ejecutar la comprobacion de `EDU_STANDARD` de la seccion 6. Revisar:

- estado `ACTIVE`;
- `vigente_desde` menor o igual a hoy;
- `vigente_hasta` nula o mayor o igual a hoy;
- ausencia de dos versiones activas superpuestas.

La salida `SELECTOR=EDU_STANDARD vN` confirma que la configuracion es visible
para ese proceso. `SELECTOR=NO_DISPONIBLE` o `SELECTOR=AMBIGUA` conserva el
fallo cerrado y debe resolverse antes de calcular una fotografia.

### Error CSRF en login

- usar exactamente `http://127.0.0.1:8001` o `http://localhost:8001`;
- confirmar que el navegador envia un origen real, no `Origin: null`;
- no abrir las paginas desde un archivo local, iframe o visor que suprima el
  origen;
- no desactivar `CsrfViewMiddleware`.

### No llega la invitacion

- confirmar que el backend local sea consola o memoria;
- revisar la terminal del servidor;
- procesar entregas recuperables:

```powershell
.\venv\Scripts\python.exe manage.py procesar_entregas_invitacion --limit 50
```

### Documento cargado pero pendiente

La carga no equivale a aprobacion. El archivo conserva estados separados de
escaneo y revision. El expediente puede enviarse a revision si el archivo esta
aportado y no tiene un bloqueo o rechazo conocido.

La identificacion no se carga desde archivos: se captura en vivo, por separado
para frente y reverso. El certificado de ingresos si se carga como PDF, JPEG o
PNG y pertenece al responsable contractual (estudiante adulto o tutor del
menor).

### Puerto ocupado

```powershell
Get-NetTCPConnection -LocalPort 8001 -ErrorAction SilentlyContinue
```

Detener el proceso anterior o usar temporalmente otro puerto:

```powershell
.\venv\Scripts\python.exe manage.py runserver 127.0.0.1:8002
```

## 10. Pruebas recomendadas antes de entregar

```powershell
.\venv\Scripts\python.exe manage.py test aprobado_web.tests.test_login_csrf
.\venv\Scripts\python.exe manage.py test financiacion_educativa.tests
git diff --check
git status --short
```
