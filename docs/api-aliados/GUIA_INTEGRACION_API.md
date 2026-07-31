# API de financiacion educativa para aliados

Version del contrato: `1.0`
Fecha: 2026-07-30

## Alcance

La API permite crear una solicitud educativa y consultar su resultado publico:

```text
POST /api/v1/financiacion-educativa/solicitudes/
GET  /api/v1/financiacion-educativa/solicitudes/{application_id}/
```

No existen webhooks en esta version. El aliado debe consultar periodicamente el
endpoint `GET`. La URL base y la credencial se entregan por un canal seguro para
cada ambiente.

## Autenticacion

Todas las peticiones requieren:

```http
Authorization: ApiKey <prefijo>.<secreto>
Accept: application/json
```

La API key debe permanecer en servidores y gestores de secretos. No debe
incluirse en frontend, aplicaciones moviles, repositorios, logs ni tickets.

## Crear una solicitud

```http
POST /api/v1/financiacion-educativa/solicitudes/
Authorization: ApiKey <prefijo>.<secreto>
Content-Type: application/json
Accept: application/json
Idempotency-Key: <clave-opaca-del-intento>
```

### Campos de entrada

| Campo | Tipo | Regla |
| --- | --- | --- |
| `external_reference` | string, max. 120 | Obligatorio; identifica la solicitud dentro de la institucion |
| `first_names` | string, max. 160 | Obligatorio |
| `last_names` | string, max. 160 | Obligatorio |
| `phone` | string | Obligatorio; `+` opcional y entre 7 y 20 digitos |
| `email` | email | Obligatorio |
| `address` | string, max. 255 | Obligatorio |
| `plan_value` | string decimal | Obligatorio, positivo, max. 14 digitos y 2 decimales |
| `term` | integer | Obligatorio, entre 1 y 32767 meses |
| `program_name` | string, max. 200 | Nombre canonico; obligatorio salvo que se envie `course_type` |
| `course_type` | string, max. 200 | Alias compatible; obligatorio salvo que se envie `program_name` |
| `document_type` | enum | Opcional: `CC`, `TI`, `CE`, `RC`, `PASSPORT`, `OTHER` |
| `document_number` | string, max. 40 | Opcional |
| `birth_date` | date | Opcional, formato `YYYY-MM-DD`, no futura |
| `enrollment_code` | string, max. 120 | Opcional |
| `academic_period` | string, max. 80 | Opcional |
| `campus` | string, max. 160 | Opcional |
| `schedule` | string, max. 80 | Opcional |
| `enrollment_date` | null | Omitir o enviar `null`; el aliado no puede asignarla |

Debe enviarse al menos uno entre `program_name` y `course_type`.
`program_name` es el nombre oficial. Si se envian ambos, deben coincidir.

`document_type`, `document_number` y `birth_date` se envian juntos o se omiten
juntos. `plan_value` y `document_number` deben enviarse como texto. Los campos
desconocidos son rechazados.

```json
{
  "external_reference": "DEMO-EDU-2026-0001",
  "first_names": "LAURA SOFIA",
  "last_names": "MARTINEZ RUIZ",
  "phone": "3000000000",
  "email": "student@example.test",
  "address": "Direccion de demostracion",
  "document_type": "CC",
  "document_number": "0000000001",
  "birth_date": "2002-08-15",
  "enrollment_code": "DEMO-MAT-001",
  "academic_period": "2026-2",
  "campus": "Sede demostracion",
  "schedule": "Nocturna",
  "program_name": "INGLES BASICO A2",
  "enrollment_date": null,
  "plan_value": "2500000.00",
  "term": 6
}
```

### Respuesta `202 Accepted`

Una solicitud nueva devuelve `RECEIVED`. Un replay devuelve el estado publico
actual de la misma solicitud, que puede haber avanzado desde su creacion.

```json
{
  "application_id": "11111111-1111-4111-8111-111111111111",
  "external_reference": "DEMO-EDU-2026-0001",
  "status": "RECEIVED",
  "course_authorized": false,
  "authorization_effective_at": null,
  "decision_reason": "",
  "created_at": "2026-07-30T10:00:00-05:00",
  "status_url": "https://api.example.test/api/v1/financiacion-educativa/solicitudes/11111111-1111-4111-8111-111111111111/",
  "first_names": "LAURA SOFIA",
  "last_names": "MARTINEZ RUIZ",
  "phone": "3000000000",
  "email": "student@example.test",
  "address": "Direccion de demostracion",
  "document_type": "CC",
  "document_number": "0000000001",
  "birth_date": "2002-08-15",
  "enrollment_code": "DEMO-MAT-001",
  "academic_period": "2026-2",
  "campus": "Sede demostracion",
  "schedule": "Nocturna",
  "program_name": "INGLES BASICO A2",
  "course_type": "INGLES BASICO A2",
  "enrollment_date": null,
  "plan_value": "2500000.00",
  "term": 6,
  "financial_terms": null
}
```

`202 Accepted` confirma recepcion y persistencia. No significa aprobacion,
entrega del correo, desembolso, pago ni firma. El enlace privado de
continuacion y su token nunca se devuelven a la institucion.

## Idempotencia

`Idempotency-Key` es obligatorio en el POST, admite hasta 255 caracteres y debe
reutilizarse al reintentar el mismo intento logico.

- Misma clave y mismo payload: `202`, misma solicitud y encabezado
  `Idempotent-Replayed: true`.
- Misma clave y payload distinto: `409 IDEMPOTENCY_CONFLICT`.
- Misma `external_reference` y payload compatible: se reutiliza la solicitud.
- Misma `external_reference` y payload incompatible:
  `409 EXTERNAL_REFERENCE_CONFLICT`.

El replay no crea otra solicitud ni reenvia la invitacion.

## Consultar el resultado

```http
GET /api/v1/financiacion-educativa/solicitudes/{application_id}/
Authorization: ApiKey <prefijo>.<secreto>
Accept: application/json
```

La respuesta `200` contiene los mismos campos de la creacion y agrega
`updated_at`. Una institucion solo puede consultar sus solicitudes. Un UUID
ajeno o inexistente devuelve el mismo `404 NOT_FOUND` y no revela propietario
ni existencia.

## Estados publicos

| Estado | Significado |
| --- | --- |
| `RECEIVED` | Solicitud recibida |
| `ACTION_REQUIRED` | El solicitante debe completar o corregir informacion |
| `UNDER_REVIEW` | Expediente en revision |
| `APPROVED` | Verificar tambien `course_authorized` |
| `REJECTED` | Solicitud rechazada |
| `CANCELLED` | Solicitud cancelada |

No se exponen estados internos.

## Decision y terminos financieros

`decision_reason` es `""` cuando no hay motivo publico. Para correcciones o
rechazos puede contener exclusivamente:

- `INCOMPLETE_INFORMATION`
- `UNREADABLE_DOCUMENT`
- `IDENTITY_MISMATCH`
- `GUARDIANSHIP_NOT_VERIFIED`
- `ENROLLMENT_NOT_VERIFIED`
- `OTHER`

Las observaciones internas no forman parte de la respuesta.

`financial_terms` es `null` mientras el curso no este autorizado. Cuando existe
una decision contractual aprobada contiene exactamente:

| Campo | Tipo | Regla |
| --- | --- | --- |
| `currency` | string | `COP` |
| `requested_amount` | string decimal | Valor solicitado, 2 decimales |
| `financed_amount` | string decimal | Capital con cargos financiados, 2 decimales |
| `term_months` | integer | Plazo contractual |
| `estimated_installment` | string decimal | Cuota fija informativa, 2 decimales |

Ejemplo generado por el motor vigente para `2.500.000 COP` a 6 meses:

```json
{
  "status": "APPROVED",
  "course_authorized": true,
  "authorization_effective_at": "2026-07-30T15:45:00-05:00",
  "decision_reason": "",
  "financial_terms": {
    "currency": "COP",
    "requested_amount": "2500000.00",
    "financed_amount": "2856778.00",
    "term_months": 6,
    "estimated_installment": "492932.00"
  }
}
```

El motor aplica originacion del 10 %, IVA del 19 % sobre originacion, fondo de
garantias del 2 %, seguro del 0,3711 %, interes del 1 % mensual, anualidad
francesa y redondeo al peso. Para el ejemplo, los cargos son `250.000`,
`47.500`, `50.000` y `9.278`; el capital financiado es `2.856.778`.

Solamente esta combinacion autoriza al aliado a activar el curso:

```json
{
  "status": "APPROVED",
  "course_authorized": true
}
```

No representa desembolso, pago ni firma de pagare.

## Errores

Todos los errores documentados usan:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "La solicitud contiene datos invalidos.",
    "fields": {
      "email": [
        "Este campo es requerido."
      ]
    }
  }
}
```

`fields` solo aparece cuando existe detalle por campo.

| HTTP | Codigos implementados |
| --- | --- |
| `400` | `VALIDATION_ERROR`; payload, campos o `Idempotency-Key` invalidos |
| `401` | `AUTHENTICATION_REQUIRED`, `INVALID_CREDENTIAL`, `CREDENTIAL_INACTIVE`, `INSTITUTION_INACTIVE` |
| `404` | `NOT_FOUND` |
| `409` | `IDEMPOTENCY_CONFLICT`, `EXTERNAL_REFERENCE_CONFLICT` |
| `405` | `METHOD_NOT_ALLOWED` |

No existe respuesta contractual `429` en esta version.

## Reintentos y seguimiento

- Ante timeout o respuesta incierta del POST, reutilizar payload e
  `Idempotency-Key`.
- Persistir `application_id` y `status_url` al recibir `202`.
- Consultar `status_url` periodicamente mediante `GET`.
- No reintentar automaticamente `400`, `401`, `404` o `409`.
- Tratar errores de red y `5xx` como transitorios según la politica del aliado.

No existen webhooks, suscripciones ni eventos salientes en esta version.

## Artefactos

- OpenAPI: `docs/api-aliados/openapi.yaml`
- Postman:
  `docs/api-aliados/postman/aprobado-financiacion-educativa.postman_collection.json`
