# Validacion manual del flujo educativo

Este procedimiento usa datos ficticios, una SQLite separada, archivos privados
temporales y correo de consola. No envia correos reales ni modifica
`db.sqlite3`.

## 1. Preparar el entorno temporal

Desde PowerShell, en la raiz del repositorio:

```powershell
$env:USE_SQLITE = 'false'
$env:DATABASE_URL = 'sqlite:///C:/tmp/aprobado-educacion-manual.sqlite3'
$env:FINANCIACION_EDUCATIVA_PRIVATE_ROOT = 'C:/tmp/aprobado-educacion-documentos'
$env:EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
$env:FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND = 'financiacion_educativa.services.entrega_invitaciones.DjangoEmailInvitationDeliveryBackend'
$env:FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND = 'financiacion_educativa.services.captura_movil.DjangoEmailMobileCaptureDeliveryBackend'
$env:DEFAULT_FROM_EMAIL = 'no-reply@example.test'

venv\Scripts\python.exe manage.py migrate
venv\Scripts\python.exe manage.py check
```

La migracion se aplica solo a
`C:\tmp\aprobado-educacion-manual.sqlite3`.

## 2. Crear configuracion de demostracion

Crear un superusuario:

```powershell
venv\Scripts\python.exe manage.py createsuperuser
$env:DEMO_ADMIN_USERNAME = '<USUARIO_SUPERADMIN_CREADO>'
```

Crear la politica financiera:

```powershell
venv\Scripts\python.exe manage.py configurar_politica_financiera_educativa --vigente-desde 2026-01-01 --activate
```

Iniciar el servidor:

```powershell
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Abrir `http://127.0.0.1:8001/admin/` y crear:

1. Una institucion activa con NIT ficticio.
2. Al menos una version obligatoria de terminos, publicarla mediante la accion
   administrativa y comprobar que quede vigente.

El admin no emite secretos de credenciales. Consultar el UUID de la institucion
y emitir una credencial mediante el servicio probado del dominio:

```powershell
venv\Scripts\python.exe manage.py listar_instituciones_api --solo-activas
venv\Scripts\python.exe manage.py emitir_credencial_institucional `
  --institucion-id '<UUID_INSTITUCION>' `
  --nombre 'Prueba manual local' `
  --mostrar-token
```

Guardar el token completo mostrado una sola vez solo durante esta validacion
local temporal.

No usar correos, identificaciones ni archivos reales.

## 3. Crear la solicitud por API

En otra consola con las mismas variables de base:

```powershell
$baseUrl = 'http://127.0.0.1:8001'
$apiKey = '<TOKEN_DEMOSTRACION>'
$idempotencyKey = [guid]::NewGuid().ToString()
$externalReference = "DEMO-$([guid]::NewGuid().ToString('N'))"
$headers = @{
    Authorization = "ApiKey $apiKey"
    'Idempotency-Key' = $idempotencyKey
}
$body = @{
    external_reference = $externalReference
    first_names = 'CAMILA'
    last_names = 'DEMO'
    phone = '3000000000'
    email = 'camila.demo@example.test'
    address = 'Direccion ficticia'
    document_type = 'CC'
    document_number = '0012345678'
    birth_date = '2002-08-15'
    enrollment_code = 'DEMO-2026-001'
    academic_period = '2026-2'
    campus = 'Sede Demo'
    schedule = 'Nocturna'
    program_name = 'Programa academico de prueba'
    enrollment_date = $null
    plan_value = '2500000.00'
    term = 6
} | ConvertTo-Json

$response = Invoke-RestMethod `
  -Method Post `
  -Uri "$baseUrl/api/v1/financiacion-educativa/solicitudes/" `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body $body
$response | ConvertTo-Json -Depth 5
```

Resultado esperado:

- HTTP `202`;
- `status: RECEIVED`;
- `course_authorized: false`;
- `financial_terms: null`;
- ausencia de `token` y `continuation_url`;
- correo HTML y texto impreso en la consola del servidor.

Repetir exactamente la peticion con el mismo cuerpo y clave:

```powershell
$replay = Invoke-WebRequest `
  -Method Post `
  -Uri "$baseUrl/api/v1/financiacion-educativa/solicitudes/" `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body $body
$replay.StatusCode
$replay.Headers['Idempotent-Replayed']
```

Resultado esperado: `202`, `Idempotent-Replayed: true`, mismo
`application_id` y ningún correo adicional.

## 4. Invitacion, cuenta y terminos

1. Abrir el enlace impreso por el backend de consola.
2. Crear la cuenta `camila.demo@example.test` o iniciar sesion con esa cuenta.
3. Confirmar la asociacion.
4. Aceptar todos los terminos vigentes.
5. Verificar que se abre el expediente.

Prueba IDOR:

1. Cerrar sesion.
2. Crear o usar otra cuenta ficticia con otro correo.
3. Abrir el enlace anterior o una URL del expediente cuyo UUID sea conocido.
4. Verificar respuesta segura `404` o pantalla generica.
5. Confirmar que no aparecen nombre, correo, celular, documento, curso ni el
   correo esperado.
6. Repetir contra terminos, expediente, finanzas, captura y descarga.

## 5. Captura desde telefono

En escritorio, la pagina de captura debe mostrar solamente el envio del enlace
movil. No debe mostrar botones para abrir camara, capturar, confirmar ni cargar
desde disco.

Enviar el enlace movil y verificar:

- redireccion `302` a la misma pagina de captura;
- mensaje de exito solo cuando el backend confirma un envio;
- nuevo correo en la consola;
- enlace temporal distinto y asociado a la misma solicitud.

Para probar una camara fisica, el telefono debe abrir la aplicacion desde un
origen HTTPS valido. `getUserMedia` no funciona normalmente desde una IP LAN
servida por HTTP. Usar un proxy HTTPS local confiable o un ambiente de prueba
seguro, ejecutar Django escuchando en la interfaz de red y usar esa misma URL
al crear la solicitud. No exponer esta base temporal a Internet.

En el telefono:

1. Abrir el enlace movil.
2. Confirmar que solicita la camara trasera.
3. Capturar y confirmar el frente.
4. Capturar y confirmar el reverso.
5. Verificar que una captura confirmada solo se reemplaza mediante la accion
   explicita `Volver a capturar`.
6. Intentar reutilizar el enlace consumido y comprobar que ya no es valido.

La deteccion del navegador y el User-Agent puede falsificarse. La proteccion
principal es el enlace temporal consumido y ligado en servidor a usuario,
solicitud y participante; la deteccion movil bloquea el flujo normal de
escritorio.

## 6. Completar expediente y finanzas

1. Registrar o revisar los datos del estudiante.
2. Para una solicitud de menor, registrar un tutor adulto y capturar las caras
   exigidas por su tipo de identificacion (frente y reverso para CC o TI).
3. Cargar un certificado de ingresos ficticio.
4. Completar los datos de matricula. El soporte adjunto es opcional; si se
   aporta, tambien debe superar escaneo y revision.
5. Abrir finanzas y crear la fotografia financiera cuando la interfaz lo
   solicite.
6. Conectar la base temporal a una instancia ClamAV de pruebas y procesar los
   documentos por el puerto real. Nunca asignar `SAFE` desde `shell`:

```powershell
venv\Scripts\python.exe manage.py procesar_escaneos_documentales --limit 20
```

7. Desde admin, aceptar los documentos y, solo si se adjunto, el soporte de
   matricula.
8. Enviar el expediente a revision.

Resultado esperado: la consulta institucional devuelve `UNDER_REVIEW`,
`course_authorized: false`.

## 7. Revision, correccion y reenvio

Abrir en admin la solicitud y usar `Revisar expediente`.

1. Elegir `Solicitar correcciones`.
2. Seleccionar un motivo controlado.
3. Seleccionar al menos un requisito pendiente.
4. Escribir un mensaje para el solicitante y una nota interna diferente.
5. Confirmar.

Resultado esperado:

- estado publico `ACTION_REQUIRED`;
- correo de decision en consola;
- el solicitante ve el mensaje, pero no la nota interna;
- el requisito sigue pendiente hasta ser actualizado despues de la decision.

El solicitante reemplaza el dato o documento indicado y vuelve a enviar el
expediente. La consulta debe regresar a `UNDER_REVIEW`.

## 8. Aprobar o rechazar

Usar dos solicitudes distintas porque aprobacion y rechazo son decisiones
finales.

Para aprobar:

1. Tener documentos seguros y aceptados, evidencia aceptada y fotografia
   financiera activa.
2. Elegir `Aprobar y autorizar curso` con motivo
   `Requisitos verificados`.
3. Confirmar el correo de decision en consola.

Consulta:

```powershell
Invoke-RestMethod `
  -Uri "$baseUrl/api/v1/financiacion-educativa/solicitudes/$($response.application_id)/" `
  -Headers @{ Authorization = "ApiKey $apiKey" } |
  ConvertTo-Json -Depth 5
```

El curso queda autorizado unicamente con:

```json
{
  "status": "APPROVED",
  "course_authorized": true
}
```

Ademas deben existir `authorization_effective_at` y `financial_terms` en COP.
La fotografia aprobada queda bloqueada.

Para rechazar una segunda solicitud:

1. Elegir `Rechazar`.
2. Seleccionar un motivo controlado distinto de requisitos verificados.
3. Escribir el mensaje para el solicitante.
4. Confirmar `status: REJECTED`, `course_authorized: false` y el correo de
   decision en consola.

## 9. Limpiar

Detener el servidor y borrar exclusivamente los recursos temporales:

```powershell
Remove-Item -LiteralPath 'C:\tmp\aprobado-educacion-manual.sqlite3' -Force
Remove-Item -LiteralPath 'C:\tmp\aprobado-educacion-documentos' -Recurse -Force
```

Antes de borrar, verificar que ambas rutas son exactamente las indicadas y que
no contienen información que deba conservarse.
