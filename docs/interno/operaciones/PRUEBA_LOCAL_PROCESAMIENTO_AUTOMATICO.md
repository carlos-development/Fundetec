# Prueba local del procesamiento automatico

Esta guia valida el loader y el recorrido automatico sin OpenAI, ClamAV, SMTP
ni ZapSign reales. Usa una base SQLite y archivos privados temporales. No debe
usarse en staging ni produccion.

## 1. Preparar settings temporales

En PowerShell, desde la raiz del repositorio:

```powershell
$env:DJANGO_LOAD_DOTENV = 'false'
$env:DEBUG = 'True'
$env:USE_SQLITE = 'True'
$env:FUNDETEC_E2E_DB_PATH = "$env:TEMP\fundetec-e2e.sqlite3"
$env:FUNDETEC_E2E_PRIVATE_ROOT = "$env:TEMP\fundetec-e2e-private"
$env:FUNDETEC_E2E_AI_BACKEND = 'financiacion_educativa.tests.ai_validation_backends.BackendIAConcluyente'
$settingsPath = "$env:TEMP\fundetec_e2e_settings.py"
@'
import os
from aprobado_web.settings import *

DEBUG = True
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': os.environ['FUNDETEC_E2E_DB_PATH'],
    }
}
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
FINANCIACION_EDUCATIVA_PRIVATE_ROOT = os.environ['FUNDETEC_E2E_PRIVATE_ROOT']
FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED = True
FINANCIACION_EDUCATIVA_DOCUMENT_SCAN_BACKEND = 'financiacion_educativa.tests.scan_backends.BackendLimpio'
FINANCIACION_EDUCATIVA_ALLOW_TEST_SCAN_BACKENDS = True
FINANCIACION_EDUCATIVA_DOCUMENT_AI_ENABLED = True
FINANCIACION_EDUCATIVA_DOCUMENT_AI_BACKEND = os.environ['FUNDETEC_E2E_AI_BACKEND']
FINANCIACION_EDUCATIVA_ALLOW_TEST_AI_BACKENDS = True
FINANCIACION_EDUCATIVA_PDF_PROCESSING_ENABLED = True
FINANCIACION_EDUCATIVA_ALLOW_TEST_CONTENT_BACKENDS = True
FINANCIACION_EDUCATIVA_CONTENT_AI_BACKEND = 'financiacion_educativa.tests.content_validation_backends.BackendContenidoConcluyente'
FINANCIACION_EDUCATIVA_CONTENT_HASH_HMAC_KEY = 'local-content-hmac-only-for-e2e'
FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND = 'financiacion_educativa.tests.signature_backends.RecordingEducationalSignatureBackend'
FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS = True
FINANCIACION_EDUCATIVA_ZAPSIGN_WEBHOOK_SECRET = 'local-webhook-only'
FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY = 'local-hmac-only'
FINANCIACION_EDUCATIVA_ACREEDOR_RAZON_SOCIAL = 'ACREEDOR LOCAL DE PRUEBA SAS'
FINANCIACION_EDUCATIVA_ACREEDOR_NIT = '900000000-1'
FINANCIACION_EDUCATIVA_ACREEDOR_REPRESENTANTE_LEGAL = 'REPRESENTANTE LOCAL'
FINANCIACION_EDUCATIVA_ACREEDOR_DOMICILIO = 'Bogota D.C.'
FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA = 'LOCAL-1'
FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION = 'CLAUSULA LOCAL DE PRUEBA.'
FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES = 'CARTA LOCAL DE PRUEBA.'
FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO = 'INCUMPLIMIENTO LOCAL DE PRUEBA.'
'@ | Set-Content -LiteralPath $settingsPath -Encoding utf8
$env:PYTHONPATH = "$env:TEMP;$PWD"
$env:DJANGO_SETTINGS_MODULE = 'fundetec_e2e_settings'
```

Los valores `local-*` y todos los backends bajo
`financiacion_educativa.tests` son inertes y exclusivos de esta prueba local.
Nunca deben copiarse a los settings ni al entorno de staging o produccion.

## 2. Crear la base y datos minimos

```powershell
venv\Scripts\python.exe manage.py migrate --noinput
venv\Scripts\python.exe manage.py configurar_politica_financiera_educativa --vigente-desde 2026-01-01 --activate
venv\Scripts\python.exe manage.py shell -c "from django.utils import timezone; from instituciones.models import Institucion; from financiacion_educativa.models import VersionTerminosFinanciacion; from financiacion_educativa.services.terminos import publicar_version_terminos; i,_=Institucion.objects.get_or_create(numero_identificacion_tributaria='901999001', defaults={'nombre_comercial':'Institucion E2E local','razon_social':'Institucion E2E Local SAS'}); v,_=VersionTerminosFinanciacion.objects.get_or_create(tipo='TERMS', version='local-e2e-v1', defaults={'titulo':'Terminos locales','contenido':'Contenido local de prueba.','obligatorio':True}); publicar_version_terminos(version=v) if v.estado != 'PUBLISHED' else None; print('INSTITUTION_ID='+str(i.pk))"
```

Emite una credencial usando el UUID mostrado. El token se imprime una sola vez:

```powershell
venv\Scripts\python.exe manage.py emitir_credencial_institucional --institucion-id <UUID> --nombre "E2E local" --prefijo e2e_local --mostrar-token
```

Guarda ese token solo en una variable privada de Postman y no en el repositorio.

## 3. Iniciar web, worker y outbox

Abre tres terminales con las variables del paso 1.

Terminal web:

```powershell
venv\Scripts\python.exe manage.py runserver 127.0.0.1:8001
```

Terminal worker, cada vez que haya trabajo:

```powershell
venv\Scripts\python.exe manage.py procesar_cola_educativa --once --limit 20 --poll-seconds 1
```

Terminal de correo, para imprimir entregas sin SMTP:

```powershell
venv\Scripts\python.exe manage.py procesar_outbox_educativo --once --limit 20
```

## 4. Crear y continuar una solicitud

En Postman:

```http
POST http://127.0.0.1:8001/api/v1/financiacion-educativa/solicitudes/
Authorization: ApiKey e2e_local.<secreto-local>
Idempotency-Key: e2e-loader-001
Content-Type: application/json
```

```json
{
  "external_reference": "E2E-LOADER-001",
  "first_names": "PERSONA",
  "last_names": "DE PRUEBA",
  "phone": "3000000000",
  "email": "persona.e2e@example.test",
  "address": "Direccion local de prueba",
  "document_type": "CC",
  "document_number": "1000000001",
  "birth_date": "2000-01-15",
  "enrollment_code": "MAT-LOCAL-001",
  "academic_period": "2026-2",
  "campus": "Sede local",
  "schedule": "Nocturna",
  "program_name": "CURSO LOCAL",
  "enrollment_date": null,
  "plan_value": "1000000.00",
  "term": 6
}
```

Ejecuta el outbox. La consola mostrara el correo inerte con el enlace de
continuacion. Abre el enlace, registra la cuenta, acepta terminos, captura ambas
caras con imagenes de prueba y carga el certificado solicitado.

Al pulsar **Enviar expediente**, el navegador debe ir inmediatamente a:

```text
/financiacion-educativa/solicitudes/<uuid>/procesamiento/
```

La pagina puede recargarse sin crear otro proceso. Ejecuta el worker y observa
el avance hasta `PENDING_SIGNATURE`. No se abre una peticion HTTP de larga
duracion y no aparece ninguna URL externa de firma.

## 5. Simular una correccion

Antes de crear otra solicitud, cambia el backend local y reinicia solo el
servidor local:

```powershell
$env:FUNDETEC_E2E_AI_BACKEND = 'financiacion_educativa.tests.ai_validation_backends.BackendIAIlegible'
```

Repite la solicitud con otra referencia e idempotency key. El worker debe dejar
el proceso en `CORRECTION_REQUIRED`. El loader muestra mensajes publicos y
enlaces para repetir cada requisito, sin codigos internos. Tras reemplazar el
ultimo elemento pendiente, debe regresar automaticamente al loader y crear una
nueva version del proceso. Para continuar hasta firma, restaura
`BackendIAConcluyente`, reinicia el servidor local y ejecuta el worker.

## 6. Simular la firma

Cuando la solicitud este en `PENDING_SIGNATURE`, consulta en la base temporal
los identificadores creados por el backend falso:

```powershell
venv\Scripts\python.exe manage.py shell -c "from financiacion_educativa.models import ProcesoFirmaEducativa; p=ProcesoFirmaEducativa.objects.filter(estado='SENT').latest('enviado_en'); print('EXTERNAL_ID='+p.external_id); print('TOKEN='+p.token_documento_externo)"
```

Envia a Postman, usando solo el secreto local inerte definido arriba:

```http
POST http://127.0.0.1:8001/api/v1/financiacion-educativa/integraciones/zapsign/webhook/
X-Educational-Signature-Secret: local-webhook-only
Content-Type: application/json
```

```json
{
  "event_type": "doc_signed",
  "token": "<TOKEN>",
  "external_id": "<EXTERNAL_ID>",
  "status": "signed"
}
```

La misma carga repetida debe ser idempotente. Al consultar el loader, el estado
debe ser `COMPLETED`, el curso debe estar autorizado y las condiciones deben
provenir de la fotografia contractual firmada.

## 7. Evidencia y limpieza

Resultados esperados:

- recarga durante `DOCUMENT_VALIDATION` conserva la etapa;
- doble clic no crea procesos adicionales;
- una correccion detiene el polling y conserva documentos aceptados;
- `PENDING_SIGNATURE` no muestra aprobacion ni condiciones financieras;
- solo el webhook firmado muestra `COMPLETED` y condiciones;
- un fallo del outbox no bloquea el loader ni reinicia el proceso.

Deten los tres procesos y elimina solo los recursos temporales declarados:

```powershell
Remove-Item -LiteralPath $env:FUNDETEC_E2E_DB_PATH -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $env:FUNDETEC_E2E_PRIVATE_ROOT -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath "$env:TEMP\fundetec_e2e_settings.py" -Force -ErrorAction SilentlyContinue
```

La prueba automatizada equivalente y mas segura es:

```powershell
venv\Scripts\python.exe manage.py test financiacion_educativa.tests.test_flujo_automatico_e2e --noinput
```
