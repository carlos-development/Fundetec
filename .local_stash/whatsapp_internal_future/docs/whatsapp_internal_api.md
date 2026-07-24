# API interna WhatsApp

Base URL:

`/api/internal/whatsapp/`

Autenticacion obligatoria en todos los endpoints:

`X-Internal-API-Key: <key>`

La llave se configura en `WHATSAPP_INTERNAL_API_KEY`. Si no esta configurada, la API rechaza las solicitudes.

Headers opcionales de observabilidad:

- `X-Request-ID`
- `X-Correlation-ID`
- `X-Idempotency-Key`

Si no llega `X-Request-ID`, el backend genera uno. Si no llega `X-Correlation-ID`, usa el `request_id`. `X-Idempotency-Key` nunca se guarda plano; solo se registra su hash.

## Productos

`product_type` separa los flujos de negocio:

- `payroll_loan`: credito de libranza existente en Aprobado.
- `whatsapp_credit`: nueva linea de credito originada por WhatsApp.

Libranza no debe mezclarse con la nueva linea WhatsApp. La API consulta libranza contra el backend existente (`Credito` y `CreditoLibranza`) y la creacion desde WhatsApp queda en un endpoint separado de staging.

## Seguridad

- Todos los endpoints requieren API Key.
- Hay rate limit por llave/IP mediante cache (`WHATSAPP_INTERNAL_API_RATE_LIMIT`, default 120/min).
- La auditoria interna registra accion, estado HTTP, IP, user agent, documento en hash SHA-256, documento enmascarado y telefono enmascarado cuando aplica.
- En creacion de solicitudes registra resultado (`created` o `validation_error`) y errores de validacion seguros por campo.
- No se registran documentos completos en auditoria.
- No se registran telefonos completos, emails completos, media completa ni payload crudo.
- El listado de documentos solo entrega metadata; no entrega archivos ni URLs.
- La validacion de identidad basica exige documento y telefono, y devuelve un token temporal de cache cuando hay coincidencia.
- El token de identidad no se guarda plano en auditoria y expira por cache (`WHATSAPP_INTERNAL_IDENTITY_TOKEN_SECONDS`, default 600).

## Observabilidad

Cada request interno emite un log estructurado JSON en el logger `gestion_creditos.internal_whatsapp` con:

- `request_id`
- `correlation_id`
- `endpoint`
- `method`
- `product_type`
- `status_code`
- `latency_ms`
- `result`
- `error_type`
- `error_fields` cuando aplica a validaciones

No se loguea payload crudo, documento completo, telefono completo, email, URLs de media ni token de identidad.

La auditoria guarda `request_id` y `correlation_id`. Si llega `X-Idempotency-Key`, guarda `idempotency_key_hash` en metadata.

Counters simples por cache:

- `whatsapp-internal-api:metrics:{endpoint}:total`
- `whatsapp-internal-api:metrics:{endpoint}:2xx`
- `whatsapp-internal-api:metrics:{endpoint}:4xx`
- `whatsapp-internal-api:metrics:{endpoint}:5xx`
- `whatsapp-internal-api:metrics:{endpoint}:latency_ms_count`
- `whatsapp-internal-api:metrics:{endpoint}:latency_ms_total`
- `whatsapp-internal-api:metrics:{endpoint}:latency_ms_last`

Errores comunes:

```json
{
  "error": "API key requerida o invalida."
}
```

```json
{
  "error": "Datos invalidos.",
  "errors": {
    "campo": "Detalle del error."
  }
}
```

Los ejemplos con `application_id`, `consent_id`, `created_at`, `identity_token` y `valid_until` contienen valores dinamicos. La estructura y nombres de campos son contractuales.

## Endpoints

### GET `/products/`

Query opcional:

```json
{
  "product_type": "payroll_loan"
}
```

Response 200:

```json
{
  "products": [
    {
      "product_type": "payroll_loan",
      "name": "Credito de libranza",
      "description": "Credito de libranza para empleados de empresas con convenio activo.",
      "current_flow": "aprobado.com.co/libranza/",
      "monthly_rate": "1.9",
      "origination_rate": "10",
      "vat_rate": "19"
    }
  ]
}
```

### POST `/simulations/`

Fuente oficial de simulacion para el bot de WhatsApp.

El bot no debe calcular cuotas, intereses, comision, IVA, total a pagar ni vigencia por su cuenta. Para `whatsapp_credit` y `payroll_loan`, debe llamar siempre a:

`POST /api/internal/whatsapp/simulations/`

Este endpoint usa las tasas y reglas parametrizadas del backend principal de Aprobado. Cualquier cambio de condiciones financieras debe hacerse en Project_aprobado, no en el bot.

Request:

```json
{
  "product_type": "whatsapp_credit",
  "amount": "1000000",
  "term_months": 6,
  "phone": "3001234567",
  "document_number": "1001234567"
}
```

Response 200:

```json
{
  "amount": "1000000.00",
  "term_months": 6,
  "origination_fee": "100000.00",
  "vat": "19000.00",
  "interest": "141004.38",
  "total_to_pay": "1260004.38",
  "monthly_payment": "210000.73",
  "valid_until": "2026-05-09",
  "warnings": []
}
```

Para `payroll_loan`, la simulacion usa la tasa de libranza del backend y agrega advertencia de convenio/validacion laboral.

Response 200 para `payroll_loan` incluye la misma estructura y:

```json
{
  "warnings": [
    "La libranza requiere convenio activo del pagador y validacion laboral."
  ]
}
```

### POST `/applications/`

Crea solo solicitudes iniciales de `whatsapp_credit`.

Request:

```json
{
  "product_type": "whatsapp_credit",
  "tipo_documento": "CC",
  "numero_documento": "1001234567",
  "nombres": "Ana",
  "apellidos": "Perez",
  "celular": "3001234567",
  "correo": "ana@example.com",
  "direccion": "Calle 123 #45-67",
  "ciudad": "Bogota",
  "ocupacion": "Independiente",
  "ingresos_mensuales": "2500000",
  "monto_solicitado": "1000000",
  "plazo_meses": 6,
  "autorizacion_tratamiento_datos": true,
  "autorizacion_validacion_informacion": true,
  "source": "whatsapp",
  "media_metadata": {
    "bank_certificate": {
      "media_id": "wamid.bank.123",
      "filename": "certificado.pdf",
      "mime_type": "application/pdf"
    },
    "id_front": {
      "media_id": "wamid.front.123",
      "filename": "cedula-frontal.jpg",
      "mime_type": "image/jpeg"
    },
    "id_back": {
      "media_id": "wamid.back.123",
      "filename": "cedula-trasera.jpg",
      "mime_type": "image/jpeg"
    }
  }
}
```

`ciudad` y `ocupacion` son aceptados por compatibilidad, pero el payload normalizado de WhatsApp Flow puede omitirlos. `direccion` es obligatoria para submissions del Flow y se guarda en metadata de la solicitud.

Reglas iniciales de `whatsapp_credit`:

- `monto_solicitado <= 2000000`
- `plazo_meses <= 6`
- no se descargan ni procesan archivos de `media_metadata`

Response 201:

```json
{
  "application_id": 1,
  "status": "received",
  "next_step": "risk_prevalidation",
  "message": "Solicitud recibida para validacion inicial del credito por WhatsApp."
}
```

Si se envia `product_type=payroll_loan`, responde 400. Libranza usa el endpoint separado.

### POST `/payroll-loan/applications/`

Inicia staging de solicitud de libranza desde WhatsApp. No reemplaza el flujo existente de formulario, documentos, validaciones y pagare.

Usa los mismos datos personales del Flow cuando apliquen y requiere `product_type=payroll_loan`.

Campos adicionales requeridos para validar convenio:

```json
{
  "empresa_id": 10
}
```

Tambien se acepta `empresa_nombre`. El backend valida:

- Empresa con convenio activo.
- Tipo de empresa `CONVENIO` o `MIXTA`.
- Vinculo laboral activo y validado por pagador para el documento.
- Si no puede validar convenio/vinculo completamente, crea staging controlado con `status=pending_payroll_validation`.

Tambien acepta `media_metadata` con las mismas llaves de `whatsapp_credit`; solo se guarda metadata.

Response 201:

```json
{
  "application_id": 2,
  "status": "pending_form_completion",
  "next_step": "continue_existing_libranza_flow",
  "message": "Solicitud de libranza iniciada. Debe continuar el flujo existente de formulario, documentos y pagare."
}
```

Response 201 cuando queda pendiente validar empresa o vinculo:

```json
{
  "application_id": 2,
  "status": "pending_payroll_validation",
  "next_step": "pending_payroll_validation",
  "message": "Solicitud de libranza recibida para validacion de convenio y vinculo laboral."
}
```

### GET `/applications/status/?document_number=&product_type=`

Response 200:

```json
{
  "application_id": 1,
  "product_type": "whatsapp_credit",
  "status": "received",
  "status_label": "Recibida",
  "source": "whatsapp",
  "created_at": "2026-05-02T10:00:00-05:00",
  "next_step": "risk_prevalidation"
}
```

Para `payroll_loan`, consulta solicitudes de libranza existentes en `Credito`.

Response 200 para libranza existente:

```json
{
  "application_id": 10,
  "product_type": "payroll_loan",
  "status": "ACTIVO",
  "status_label": "Activo",
  "source": "aprobado_backend",
  "created_at": "2026-05-02T10:00:00-05:00",
  "next_step": "credit_active"
}
```

### GET `/credits/status/?document_number=&product_type=payroll_loan`

Response 200:

```json
{
  "has_active_credit": true,
  "product_type": "payroll_loan",
  "credit_reference": "CR-2026-00001",
  "status": "ACTIVO",
  "status_label": "Activo",
  "next_payment_date": "2026-06-01",
  "days_past_due": 0
}
```

No entrega saldos, montos aprobados ni documentos.

### GET `/documents/?document_number=&product_type=`

Response 200:

```json
{
  "documents": [
    {
      "product_type": "payroll_loan",
      "document_type": "certificado_bancario",
      "label": "Certificado bancario",
      "available": true,
      "delivery": "not_available_without_strong_identity_validation"
    }
  ]
}
```

Este endpoint no entrega archivos ni URLs.

## Media Metadata

Llaves soportadas:

- `bank_certificate`
- `id_front`
- `id_back`

Cada llave debe ser un objeto JSON. Ejemplo:

```json
{
  "media_metadata": {
    "bank_certificate": {
      "media_id": "wamid.bank.123",
      "filename": "certificado.pdf",
      "mime_type": "application/pdf"
    }
  }
}
```

El backend no descarga, no valida contenido y no procesa archivos todavia. Guarda solo estos campos seguros por archivo:

- `media_id`
- `filename`
- `mime_type`
- `field_name`
- `received_at`

No guarda URLs publicas, `download_url` ni campos extra de media.

La metadata queda en `WhatsAppInternalApplication.metadata` junto con:

```json
{
  "media_processing": "pending_not_downloaded"
}
```

TODO documental: no existe todavia un modelo documental especifico para submissions de WhatsApp Flow. Cuando se defina validacion fuerte de identidad y procesamiento de medios, crear una entidad documental separada o conectar explicitamente con el modelo documental final sin usar rutas/archivos directos en esta API.

Para libranza puede devolver varios documentos disponibles, siempre con esta estructura por item:

```json
{
  "product_type": "payroll_loan",
  "document_type": "certificado_bancario",
  "label": "Certificado bancario",
  "available": true,
  "delivery": "not_available_without_strong_identity_validation"
}
```

### POST `/identity/validate/`

Request:

```json
{
  "document_number": "1001234567",
  "phone": "3001234567"
}
```

Response 200:

```json
{
  "identity_validated": true,
  "identity_token": "token-temporal",
  "expires_in_seconds": 600
}
```

Si documento y telefono no coinciden con una solicitud o credito conocido:

```json
{
  "identity_validated": false,
  "identity_token": null,
  "expires_in_seconds": 0
}
```

Auditoria:

- Guarda documento hasheado.
- Guarda documento enmascarado.
- Guarda telefono enmascarado.
- Guarda resultado (`validated` o `not_validated`).
- No guarda `identity_token`, telefono plano, documento plano ni payload crudo.

### POST `/consents/`

Request:

```json
{
  "product_type": "whatsapp_credit",
  "document_number": "1001234567",
  "phone": "3001234567",
  "consent_type": "tratamiento_datos",
  "accepted": true,
  "text_version": "v1"
}
```

Response 201:

```json
{
  "consent_id": 1,
  "status": "registered",
  "message": "Consentimiento registrado."
}
```

## Responsabilidades

Backend Aprobado:

- Mantiene la logica financiera de simulacion.
- Expone estados de libranza y credito WhatsApp.
- Registra auditoria y consentimientos.
- Valida convenio y vinculo laboral para staging de libranza.

Bot WhatsApp:

- Consume la API con API Key.
- No calcula cuotas por su cuenta.
- No almacena ni expone archivos sensibles sin validacion fuerte adicional.
- Debe enviar `product_type` correcto segun flujo.

## Fixtures de pruebas

Los tests de integracion usan fixtures/factories locales en:

`gestion_creditos/tests/whatsapp_internal_api_fixtures.py`

Incluyen:

- `create_whatsapp_credit_fixture()`: solicitud inicial `whatsapp_credit`.
- `create_payroll_loan_fixture()`: empresa con convenio, vinculo laboral validado, credito activo de libranza y metadata documental.
- `whatsapp_application_payload()`: payload base para `POST /applications/`.
- `payroll_application_payload()`: payload base para `POST /payroll-loan/applications/`.

## Cobertura esperada

La suite `gestion_creditos.tests.test_whatsapp_internal_api` cubre:

- API Key requerida en todos los endpoints.
- Integracion con header `X-Internal-API-Key` en todos los endpoints.
- Contratos JSON de productos, simulacion, creacion de solicitudes, estados, documentos, identidad y consentimientos.
- Separacion entre `payroll_loan` y `whatsapp_credit`.
- No entrega de archivos, URLs, saldos ni montos sensibles.
- Auditoria con documento hasheado/enmascarado.
- Auditoria de identidad con telefono enmascarado y sin token plano.
- Expiracion de `identity_token` en cache.
- Payload normalizado de WhatsApp Flow para `whatsapp_credit`.
- Staging de libranza desde WhatsApp Flow con `empresa_id` o `empresa_nombre`.
- Staging controlado de libranza cuando falta validar empresa o vinculo.
- Rechazo de `product_type` invalido.
- Rechazo de `media_metadata` malformado.
- Normalizacion de `media_metadata` sin URLs publicas.
- Auditoria de errores con documento enmascarado y sin payload crudo.

## Proximos pasos

- Riesgo: conectar `whatsapp_credit` con evaluacion formal de riesgo, reglas de rechazo y trazabilidad de decision.
- Desembolso: definir estados, controles operativos y proveedor de pago antes de activar desembolsos desde WhatsApp.
- Pagare: conectar libranza y `whatsapp_credit` con flujo de firma electronica cuando aplique.
- Validacion fuerte de identidad: definir mecanismo antes de entregar archivos, saldos, cuotas o datos sensibles.
- Libranza WhatsApp: conectar staging de libranza con formulario existente, validaciones documentales y firma de pagare.
- Rotar y custodiar `WHATSAPP_INTERNAL_API_KEY` fuera del repositorio.
