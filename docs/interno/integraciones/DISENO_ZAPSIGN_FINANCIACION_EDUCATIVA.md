# Integracion ZapSign de financiacion educativa

## Estado implementado

La integracion educativa es independiente de `gestion_creditos`. Su fuente de
verdad esta en:

- `services/artefactos_contractuales.py`: pagare y ficha de matricula;
- `services/firma_zapsign.py`: puerto de firma, adaptador y webhook;
- `ProcesoFirmaEducativa`: envio y estado del documento;
- `EventoWebhookFirmaEducativa`: deduplicacion sin almacenar el payload;
- `ArtefactoContractualEducativo`: original y PDF firmado privados.

El backend predeterminado es `DisabledEducationalSignatureBackend`. Ningun
entorno llama a ZapSign hasta configurar expresamente el backend real, sus
secretos y los system checks. Las variables educativas no reutilizan
`ZAPSIGN_*` del producto historico.

## Secuencia vigente

1. Cada carga se escanea y valida por los puertos documentales configurados.
2. Un expediente concluyente avanza automaticamente; solo los resultados
   inciertos o tecnicamente fallidos llegan a revision administrativa.
3. La decision documental crea y bloquea la fotografia financiera definitiva.
4. La solicitud pasa a `PENDING_PROMISSORY_NOTE` y genera pagare y ficha
   versionados desde datos reales.
5. `preparar_proceso_firma` crea un proceso idempotente para el pagare vigente.
6. Con `FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=true`, la orquestacion reclama
   el envio con bloqueo y limite de intentos.
7. El adaptador transmite el PDF en base64 y un unico responsable contractual.
8. La solicitud pasa a `PENDING_SIGNATURE` despues de confirmar la creacion.
9. El webhook autentica el secreto, valida `token` y `external_id`, y deduplica
   por identificador del proveedor o una huella canonica estable del evento.
10. `doc_signed` exige estado firmado en el proveedor, recupera el PDF, valida
   formato/tamano, guarda hash y archivo privado y transiciona a `APPROVED`.
11. Solo entonces la API publica `course_authorized=true` y `financial_terms`.
12. `doc_refused` invalida el pagare, vuelve a `PENDING_PROMISSORY_NOTE` y
    permite generar una version nueva.

**Enviar a revision no envia un pagare.** El envio ocurre solo despues de una
decision documental concluyente, automatica o manual.

## Configuracion educativa

El contrato completo esta en `.env.example`. Las variables principales son
`FINANCIACION_EDUCATIVA_ZAPSIGN_BACKEND`, `*_BASE_URL`, `*_API_TOKEN`,
`*_WEBHOOK_SECRET`, `*_WEBHOOK_HEADER`, `*_TIMEOUT_SECONDS`,
`*_MAX_ATTEMPTS`, `*_STALE_SECONDS`, `*_AUTH_MODE`, `*_REQUIRE_SELFIE`,
`*_SELFIE_VALIDATION_TYPE` y
`FINANCIACION_EDUCATIVA_SIGNATURE_RECIPIENT_HMAC_KEY`.

El endpoint privado es
`/api/v1/financiacion-educativa/integraciones/zapsign/webhook/`; esta excluido
del OpenAPI institucional. El secreto debe llegar en el header configurado. Si
no existe secreto, el endpoint falla cerrado con 503.

Adulto: el estudiante es el responsable contractual y unico firmante. Menor:
el tutor registrado como deudor principal es el unico firmante. El correo se
toma del participante contractual; no se sustituye silenciosamente por el del
estudiante.

## Seguridad e idempotencia

- No se guardan el payload, headers, correo ni contenido de los documentos en
  eventos o admin.
- El correo queda representado mediante HMAC con clave exclusiva.
- Los archivos originales y firmados usan almacenamiento privado.
- Los timeouts y errores de proveedor dejan el proceso recuperable sin cambiar
  la solicitud a firmado.
- Un envio `SENDING` puede recuperarse despues de `*_STALE_SECONDS` y respeta
  `*_MAX_ATTEMPTS`.
- Un timeout o respuesta ambigua del POST real se marca
  `SIGNATURE_SEND_AMBIGUOUS` y no se reenvia automaticamente: primero debe
  conciliarse el `external_id` con el proveedor para evitar dos documentos.
- Un rechazo HTTP 4xx tampoco se reintenta automaticamente. Despues de corregir
  la configuracion o el payload, un operador puede usar
  `enviar_pagares_educativos --confirmar-reintento-permanente`; esta opcion no
  habilita reenvios de resultados ambiguos.
- La confirmacion aplica bloqueo transaccional y el historial registra cada
  transicion.
- La URL de descarga debe usar HTTPS y un host permitido del proveedor. La
  descarga no sigue redirecciones, usa streaming y aplica el limite antes de
  acumular el archivo completo en memoria.

Los adaptadores falsos solo se admiten en pruebas cuando
`FINANCIACION_EDUCATIVA_ALLOW_TEST_SIGNATURE_BACKENDS=true`. El check de Django
rechaza esa configuracion en un entorno normal.

## Operacion

El recorrido automatico se habilita de forma explicita con:

```text
FINANCIACION_EDUCATIVA_AUTOMATION_ENABLED=true
```

La habilitacion exige ademas los datos legales del acreedor y la version y
clausulas juridicas educativas declaradas en `.env.example`. El sistema no
genera el pagare con textos provisionales: una variable vacia detiene la
generacion antes de crear o enviar el artefacto.

Al completar el expediente, el callback posterior al commit ejecuta ClamAV,
validacion IA, fotografia, artefactos y envio. Si cualquier proveedor falla, el
estado persistido queda disponible para recuperacion con:

```powershell
python manage.py procesar_orquestacion_educativa --solicitud-id UUID --limit 1
```

Los comandos especializados y el admin se conservan para diagnostico o
recuperacion puntual:

```powershell
python manage.py enviar_pagares_educativos --solicitud-id UUID --limit 1
```

La ausencia de cola obligatoria es deliberada. El callback no es ejecucion
asincrona y puede aumentar la latencia del POST que completa el expediente. El
comando de orquestacion puede programarse externamente para recuperar fallos o
procesos interrumpidos; los intentos persistentes evitan trabajo concurrente
duplicado entre procesos Django cooperantes.

## Pendientes antes de habilitar un proveedor real

- revision juridica del texto contractual y de la representacion del menor;
- confirmar NIT, representante legal y domicilio del acreedor;
- validar en sandbox el evento y header acordados para la cuenta contratada;
- confirmar modos de autenticacion, selfie y documentos disponibles en el plan;
- definir retencion, custodia y recuperacion operacional del firmado;
- probar recuperacion ante el caso excepcional en que ZapSign crea el documento
  pero el proceso local termina antes de guardar su token.
