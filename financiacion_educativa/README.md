# Financiacion educativa

Dominio aislado para solicitudes educativas originadas por instituciones. No
depende de servicios de `gestion_creditos`.

## Credenciales de la API institucional

La API autentica `Authorization: ApiKey <prefijo>.<secreto>` mediante
`InstitutionApiKeyAuthentication`. El modelo persiste el prefijo y un hash de
Django, nunca el secreto recuperable. Emision, listado, rotacion y revocacion
se operan exclusivamente con estos comandos:

```text
listar_instituciones_api
emitir_credencial_institucional
listar_credenciales_institucionales
rotar_credencial_institucional
revocar_credencial_institucional
```

Emision y rotacion exigen escoger entre `--mostrar-token` y
`--archivo-token`. La segunda opcion crea un archivo nuevo `0600`, no lo
sobrepone y no imprime el token. La guia completa para staging esta en
`docs/interno/operaciones/MANTENIMIENTO_STAGING.md`.

`APROBADO_INSTITUTION_API_KEY` es una variable heredada y no emite ni
autentica estas credenciales. Los `alcances` almacenados son metadatos: los
permisos actuales aun no implementan autorizacion por alcance.

## Orquestacion de solicitud e invitacion

La creacion institucional entra por
`POST /api/v1/financiacion-educativa/solicitudes/` y se ejecuta mediante
`crear_solicitud_institucional_orquestada`.

1. La solicitud y su registro de idempotencia se crean en una transaccion.
2. Solo una solicitud nueva genera una invitacion inicial y una
   `EntregaInvitacionContinuacion` pendiente.
3. Un callback registrado con `transaction.on_commit` entrega el enlace por
   correo mediante el puerto propio del dominio.
4. El callback captura los fallos del backend y marca la entrega como fallida
   sin convertir la respuesta HTTP 202 en un error.
5. Un replay, incluso con otra clave idempotente y la misma referencia
   compatible, devuelve la solicitud existente sin crear ni reenviar una
   invitacion.

`transaction.on_commit` se ejecuta en el proceso de la peticion y no constituye
procesamiento asincronico. El timeout del backend limita el tiempo de espera.

## Recuperacion y reemision

Ejecutar el comando:

```powershell
venv\Scripts\python.exe manage.py procesar_entregas_invitacion --limit 50
```

El comando selecciona entregas fallidas o estancadas. Como el token y la URL no
se persisten, la recuperacion crea una invitacion nueva, revoca la anterior y
marca su entrega como reemplazada dentro de la misma transaccion. Nunca intenta
reconstruir el enlace previo.

La reemision manual requiere un usuario administrativo. Tanto la reemision
manual como la automatica respetan el cooldown y el limite por ventana. En todo
momento puede existir como maximo una invitacion activa por solicitud.

Las solicitudes creadas antes de esta orquestacion no se procesan
automaticamente. Pueden atenderse solo mediante una accion administrativa
explicita.

## Configuracion

| Variable | Predeterminado | Uso |
| --- | ---: | --- |
| `FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS` | `72` | Vigencia del enlace |
| `FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT` | `5` | Reemisiones por ventana |
| `FINANCIACION_EDUCATIVA_INVITATION_REISSUE_WINDOW_HOURS` | `24` | Ventana del limite |
| `FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS` | `300` | Espera entre emisiones |
| `FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_TIMEOUT_SECONDS` | `10` | Timeout del correo |
| `FINANCIACION_EDUCATIVA_INVITATION_RECOVERY_STALE_SECONDS` | `300` | Umbral de entrega estancada |
| `FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY` | `SECRET_KEY` | Clave del HMAC de destinatario |
| `FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND` | Backend de correo del dominio | Puerto de entrega |
| `FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TTL_MINUTES` | `30` | Vigencia del enlace movil |
| `FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_COOLDOWN_SECONDS` | `120` | Espera entre solicitudes |
| `FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_LIMIT` | `5` | Emisiones por ventana |
| `FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_REISSUE_WINDOW_HOURS` | `1` | Ventana del limite |
| `FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_TOKEN_HMAC_KEY` | `SECRET_KEY` | Clave HMAC del enlace movil |
| `FINANCIACION_EDUCATIVA_MOBILE_CAPTURE_DELIVERY_BACKEND` | Backend propio del dominio | Entrega del enlace movil |

El backend del dominio usa exclusivamente el backend configurado globalmente
en Django y no importa servicios del dominio historico. Para envios reales se
exige `django.core.mail.backends.smtp.EmailBackend`, una unica modalidad TLS o
SSL, puerto coherente, timeout positivo, credencial y remitente coincidentes.
La referencia sin secretos esta en `.env.example`.

La configuracion predeterminada usa SMTP de Gmail (`smtp.gmail.com`, TLS,
puerto 587 y timeout de 10 segundos). Requiere `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD` y `DEFAULT_FROM_EMAIL`. Disponibilidad, costo y cuotas
dependen de la cuenta Google o Google Workspace contratada; SMTP no implica
gratuidad ni capacidad ilimitada. Las pruebas automatizadas usan
`django.core.mail.backends.locmem.EmailBackend` y nunca realizan envios reales.

## Continuacion de captura desde celular

La pantalla de camara permite solicitar por POST un enlace temporal al correo
registrado. Usa 48 bytes aleatorios, persiste solo HMAC SHA-256, no incluye el
UUID de la solicitud ni rutas de documentos, exige la cuenta propietaria y se
consume una vez. Una nueva emision revoca la anterior y los eventos no guardan
correo, token, URL ni contenido del mensaje.

Si SMTP no confirma la aceptacion, el enlace nuevo queda fallido y revocado, la
interfaz no muestra exito y el estado funcional de la solicitud no cambia.

Las nueve muestras visuales inertes se pueden enviar solo a un destinatario
explicito y autorizado:

```powershell
venv\Scripts\python.exe manage.py enviar_correos_prueba_educacion `
  --destinatario correo-autorizado@example.com `
  --confirmar
```

El comando valida SMTP, no consulta solicitudes, no crea tokens y no cambia
estados. Todos los asuntos comienzan con `[PRUEBA]`.

El token se entrega en el fragmento de la URL (`#...`), que no viaja en la
peticion HTTP ni aparece en access logs. La pagina de handoff lo retira de la
barra y lo envia una sola vez mediante POST protegido por CSRF antes de
redirigir a autenticacion.

El certificado de ingresos permanece obligatorio para revision manual: del
estudiante/deudor adulto o del tutor/deudor principal cuando el estudiante es
menor. Admite PDF, JPEG y PNG, almacenamiento privado y reemplazo trazable.
OCR, IA, scoring y aprobacion automatica quedan para una fase posterior.

## Seguridad

- La API institucional nunca devuelve el enlace ni el token.
- Solo se almacena el hash del token en la invitacion.
- La entrega almacena un HMAC con clave del destinatario, no el correo.
- Eventos, errores controlados y administracion no contienen token, URL ni
  contenido del mensaje.
- Los enlaces vencen, se consumen una vez y las respuestas invalidas no
  permiten enumerar solicitudes.
- La asociacion exige que el correo normalizado de la cuenta coincida con el
  correo de la solicitud y nunca reemplaza un propietario existente.
- Todas las rutas web, descargas y operaciones POST vuelven a comprobar
  propiedad por usuario y correo. La posesion de un UUID no concede acceso.
- Un usuario interno solo accede al expediente desde vistas que admiten
  revision y con el permiso explicito
  `financiacion_educativa.revisar_solicitud_financiacion`.
- Los intentos de acceso cruzado y reasociacion se registran sin tokens ni
  datos personales.

## Persistencia y concurrencia

La migracion `0006` crea el outbox y sus restricciones:

- secuencia unica por solicitud;
- una sola entrega de origen `INITIAL` por solicitud;
- una sola entrega pendiente o en envio por solicitud.

SQLite valida las restricciones y el flujo funcional. La prueba de carrera con
`select_for_update` se omite localmente y debe ejecutarse en staging con
PostgreSQL, donde existen bloqueos de fila reales.

## Contrato institucional de la solicitud

La API conserva el contrato plano de
`POST /api/v1/financiacion-educativa/solicitudes/`. Los campos nuevos son
opcionales para mantener compatibilidad con instituciones integradas, pero la
identidad debe enviarse como bloque completo cuando se utiliza.

| Campo | Requerido | Formato y significado |
| --- | --- | --- |
| `external_reference` | Si | Referencia unica dentro de la institucion |
| `first_names`, `last_names` | Si | Nombres del estudiante |
| `phone`, `email`, `address` | Si | Contacto del estudiante |
| `document_type` | Condicional | `CC`, `TI`, `CE`, `RC`, `PASSPORT` u `OTHER` |
| `document_number` | Condicional | Texto; conserva ceros iniciales |
| `birth_date` | Condicional | ISO 8601 `YYYY-MM-DD`, no futura |
| `enrollment_code` | No | Codigo institucional de matricula |
| `academic_period` | No | Periodo academico |
| `campus`, `schedule` | No | Sede y jornada |
| `program_name` | Si | Nombre completo, por ejemplo `INGLÉS BÁSICO A2 DIAMANTE` |
| `course_type` | Alias | Alias compatible de `program_name`; no puede contradecirlo |
| `enrollment_date` | No | Solo acepta `null`; se reserva para la firma valida del pagare |
| `plan_value` | Si | Texto decimal positivo en COP |
| `term` | Si | Plazo positivo en meses |

`document_type`, `document_number` y `birth_date` deben aparecer juntos. La
respuesta `202` y la consulta `GET` devuelven explicitamente estos campos. El
endpoint de detalle filtra siempre por la institucion autenticada; una
institucion ajena recibe `404`.

La unicidad existente permanece en
`(institucion, external_reference)`. Documento y correo no son unicos porque
un estudiante puede tener solicitudes distintas. La clave de idempotencia
incluye todos los datos del contrato ampliado y una referencia existente con
datos incompatibles produce `409`.

La migracion `0007` agrega a la solicitud la identidad institucional, codigo,
periodo, sede, jornada y `fecha_matricula`. No duplica el programa: se conserva
`nombre_curso` como fuente de `program_name`.

## Fecha de matricula y firma

`fecha_matricula` permanece nula hasta que el webhook autenticado confirma la
firma del pagare vigente. La API rechaza un valor inicial no nulo, las vistas no
la escriben y el campo no es editable en el admin. El servicio de firma registra
la fecha local de manera transaccional e idempotente; abrir el documento, un
envio fallido o un evento ajeno no la modifican.

## Estudiante, persona relacionada y tutor

La persona enviada por la API siempre es el estudiante titular. Cuando la
identidad institucional esta completa, al aceptar terminos se crea su
participante sin volver a pedir esos datos.

La "persona relacionada" es el tutor o representante adulto adicional. No
reemplaza al estudiante. El backend calcula la edad a la fecha de creacion de
la solicitud usando `FINANCIACION_EDUCATIVA_MAYORIA_EDAD` (18 por defecto):

- adulto: estudiante y posible deudor principal; no se habilita tutor;
- menor: estudiante titular; se exige tutor adulto, relacion declarada y su
  documento, y el tutor actua como deudor principal.

No existe una regla institucional o por programa configurada en el dominio
actual, por lo que no se inventa una.

## Documentos y revision

La carga local valida tamano, contenido no vacio, MIME real, extension
coherente, limite por solicitud, hash duplicado y nombre privado aleatorio.
Despues mantiene dos decisiones independientes:

- `estado_escaneo`: pendiente, seguro o bloqueado por un escaner externo;
- `estado_validacion`: pendiente, aceptado o rechazado por revision.

El dominio dispone de puertos independientes para ClamAV e IA visual. Ambos
estan deshabilitados por defecto y se habilitan por ambiente. ClamAV procesa
todos los archivos. La IA solo recibe JPEG/PNG seguros y persiste por intento
calidad, legibilidad, confianza, correspondencia de tipo, consistencia y un
resultado estructurado versionado. Para identificaciones exige ademas documento
de identidad, documento colombiano, lado correcto, campos visibles y ausencia
de desenfoque, oscuridad, reflejo, recorte u obstruccion grave. El resultado
estructurado conserva tipo, numero y nombres visibles con longitud y caracteres
controlados para que un revisor autorizado pueda contrastarlos; esos datos no
se incluyen en logs ni eventos.

La IA valida contenido y calidad visual; no prueba autenticidad fisica ni
liveness. Una contradiccion visual concluyente, como un objeto que no es una
identificacion o el lado incorrecto, solicita una nueva captura. Baja confianza,
posible imagen no real, inconsistencia o fallo tecnico pasa a revision manual y
nunca equivale a aprobacion. Cuando el procesamiento de contenido esta
habilitado, los PDF de ingresos y matricula se validan estructuralmente,
extraen texto y renderizan solo paginas limitadas antes de clasificarse. Un PDF
seguro y concluyente puede continuar; un formato corregible solicita reemplazo,
un fallo temporal se reintenta y una ambiguedad real queda como excepcion
manual. Con la funcion deshabilitada se conserva el comportamiento anterior.
La aprobacion visual no cambia las banderas `identidad_verificada` ni
`relacion_verificada`; estas quedan reservadas para una verificacion fuerte o
una decision humana que realmente pueda respaldarlas.

Al completar el expediente, el request crea de forma transaccional un proceso
persistente en `QUEUED`. Un worker educativo independiente reclama una etapa a
la vez con lease y `select_for_update(skip_locked=True)`. ClamAV, OpenAI,
WeasyPrint y ZapSign no se ejecutan dentro del worker HTTP ni en callbacks
`transaction.on_commit`. Los intentos y veredictos quedan auditados; los
comandos se conservan para operacion, diagnostico y recuperacion.

`POST .../documentacion/completar/` comprueba propiedad, CSRF, terminos,
participantes y la politica documental unica. Cuando todos los soportes son
concluyentes, la orquestacion bloquea una sola fotografia financiera activa,
genera el paquete contractual `PAQUETE-EDU-3.0` con pagare, carta de
instrucciones, Habeas Data y ficha `FO-AD-005 V2`, prepara un unico firmante y
envia ese unico PDF mediante el puerto educativo de ZapSign. La solicitud queda
`PENDING_SIGNATURE`; solo un webhook
valido para el pagare vigente puede llevarla a `APPROVED`, autorizar el curso y
publicar `financial_terms`.

La identificacion del estudiante y, cuando aplica, del tutor se obtiene en dos
evidencias separadas: frente y reverso. En escritorio solo se ofrece enviar un
enlace temporal al correo propietario. Los controles de `getUserMedia` se
renderizan cuando existe un contexto movil consumido, vigente y ligado en
servidor a usuario, solicitud y participante. La camara solicita
`facingMode: environment` y ofrece un `input` movil con `capture=environment`
como fallback cuando `getUserMedia` no esta disponible. Antes de confirmar, el
navegador verifica resolucion minima, iluminacion y desenfoque de forma
conservadora; no afirma autenticidad. Una captura confirmada solo se reemplaza
mediante confirmacion explicita. El backend exige
origen `CAMERA`, admite JPEG o PNG y bloquea la carga convencional para estos
tipos. En produccion depende de HTTPS, permiso del navegador y una camara
disponible.

La aplicacion web no puede probar criptograficamente que un cliente manipulado
obtuvo los bytes desde el sensor y el User-Agent puede falsificarse. Para el
acceso directo, la proteccion principal es la sesion autenticada, la propiedad
de la solicitud, su estado editable y el rol participante; la deteccion movil
solo decide la experiencia. El grant temporal conserva esas mismas reglas para
el handoff desde computador. La autenticidad del documento sigue sujeta a
controles tecnicos adicionales y revision humana.

El soporte de ingresos o certificacion bancaria es una carga privada obligatoria
en PDF, JPEG o PNG. Corresponde al estudiante deudor cuando es adulto y al tutor
deudor cuando el estudiante es menor. Siempre requiere escaneo tecnico. JPEG,
PNG y PDF se clasifican por contenido cuando el procesador esta habilitado. Se
admiten certificado laboral, certificado de ingresos, ingresos y retenciones,
extracto bancario, certificacion bancaria de titularidad y desprendible de
nomina. La certificacion de titularidad no requiere movimientos ni valores
financieros; no se exige ingreso minimo, no se calcula score y no se persisten
movimientos ni numeros de cuenta completos.

El procesamiento PDF usa `pypdf` para estructura y texto, y `pypdfium2` para
renderizado en memoria. Rechaza cifrado, corrupcion, contenido activo, adjuntos
y limites excedidos. La traza `ProcesamientoContenidoDocumento` conserva hash,
versiones, paginas, clasificacion, campos minimos y razones controladas, nunca
el PDF duplicado, imagenes renderizadas, prompts o respuestas completas.

Los PDF, JPEG y PNG se pueden previsualizar desde un visor modal. El endpoint
privado comprueba sesion, propiedad de la solicitud, pertenencia del documento,
estado activo y MIME permitido. La respuesta es `inline`, no publica la ruta
de almacenamiento y aplica `no-store`, `nosniff`, `SAMEORIGIN`,
`frame-ancestors 'self'`, `object-src 'none'` y proteccion de mismo origen. No
usa la directiva CSP `sandbox` porque Chrome no puede cargar su visor PDF en un
frame de origen opaco. Otros tipos solo conservan la descarga protegida.

## Configuracion financiera inicial

No se crea una politica mediante migracion de datos. En un ambiente nuevo se
debe ejecutar de forma explicita:

```powershell
venv\Scripts\python.exe manage.py configurar_politica_financiera_educativa `
  --vigente-desde 2026-01-01 `
  --policy-version 1 `
  --activate
```

El comando es idempotente. Crea `EDU_STANDARD` con interes mensual del 1 %,
anualidad francesa, originacion del 10 %, IVA del 19 % sobre originacion,
fondo de garantia del 2 %, seguro vida deudores del 0,3711 %, cargos financiados y redondeo
COP al peso. Nunca selecciona politicas futuras, vencidas o inactivas y falla
de forma cerrada ante superposiciones.

Las etiquetas comerciales son `Fondo de garantia` y `Seguro vida deudores`.
Los valores tecnicos persistidos de proveedor (`Figarantias` y `SURA`) se
conservan exclusivamente por compatibilidad interna y no cambian formulas ni
contratos de API.

Para diagnosticar el proceso actual sin modificar datos ni imprimir
credenciales:

```powershell
venv\Scripts\python.exe manage.py diagnosticar_politica_financiera_educativa
```

El comando muestra el modulo de settings, motor e identificador seguro de base,
zona horaria, fecha local, versiones `EDU_STANDARD` y resultado del selector.
El selector consulta la base en cada operacion: activar una politica no requiere
reiniciar Django. Si dos procesos discrepan, se debe ejecutar el diagnostico
desde la misma terminal y configuracion que inicia cada uno.

El admin muestra si cada version es aplicable hoy y alerta cuando falta o hay
ambiguedad. La interfaz del estudiante presenta un mensaje operativo sin
detalles sensibles. Los diagnosticos tecnicos solo registran codigo de
politica, fecha y tipo de inconsistencia.

El comando oficial es la operacion recomendada para crear de manera repetible la
politica inicial. Django Admin se usa para versiones posteriores: el operador
crea el borrador con los valores aprobados y lo activa mediante la accion
administrativa. Ambos caminos usan `activar_configuracion_financiera`, que
valida vigencias y superposiciones.

## Proyecciones y administracion

Las proyecciones de abono y liquidacion total son calculos informativos sobre
la fotografia financiera activa. No crean pagos, comprobantes ni movimientos,
no cambian saldos y no sustituyen una liquidacion contractual. La interfaz
muestra el resultado en la misma pantalla y lo anuncia mediante una
notificacion accesible.

Django Admin permite consultar solicitudes, participantes, consentimientos,
documentos, evidencias, invitaciones, historial, fotografias y cuotas. Tambien
permite reemitir o revocar invitaciones, publicar terminos, gestionar versiones
financieras y aceptar o rechazar documentos y evidencias cuando cumplen las
precondiciones del servicio. La vista protegida `Revisar expediente` exige el
permiso explicito de revision y permite aprobar, rechazar o solicitar
correcciones con motivo y requisitos controlados.

La decision manual queda inmutable con responsable y fecha. Se usa solo cuando
la automatizacion no obtiene una conclusion suficiente. Aprobar el expediente
exige la misma politica documental que el recorrido automatico; despues genera
la fotografia y los artefactos, pero el estado final sigue reservado para la
firma. Rechazo y correccion exponen solo un codigo
controlado y el mensaje para el solicitante. La observacion interna no sale por
API ni por la interfaz del solicitante. Una correccion solo se considera
resuelta cuando cada dato o documento señalado se actualiza despues de la
decision.

Los estados publicos institucionales son `RECEIVED`, `ACTION_REQUIRED`,
`UNDER_REVIEW`, `APPROVED`, `REJECTED` y `CANCELLED`. Solo la combinacion
`APPROVED` y `course_authorized=true` autoriza activar el curso. No representa
un desembolso.

Cada decision crea una intencion en el outbox educativo dentro de la misma
transaccion que la decision. Un worker independiente reclama y entrega despues
del commit, sin SMTP en el request ni en `transaction.on_commit`. Un fallo de
correo no revierte ni cambia la decision. La semantica, recuperacion y limites
de SMTP se documentan en
`docs/interno/operaciones/OUTBOX_CORREOS_EDUCATIVOS.md`.

El backend real de antivirus y el adaptador OpenAI se seleccionan por settings.
Los dobles incluidos en `tests` estan bloqueados fuera del modo explicito de
pruebas.

## Artefactos contractuales

Los artefactos nuevos usan
`templates/financiacion_educativa/documentos/paquete_contractual_v3.html` y el
membrete versionado del modulo. El PDF contiene, en orden, pagare, carta de
instrucciones, Habeas Data y ficha de matricula. Los artefactos historicos
conservan su archivo y version anterior. Los apartados que requieren aprobacion
juridica fallan de forma cerrada y se inyectan exclusivamente desde
configuracion juridica versionada:

- `FINANCIACION_EDUCATIVA_PAGARE_VERSION_JURIDICA`;
- `FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_OBLIGACION`;
- `FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_CARTA_INSTRUCCIONES`;
- `FINANCIACION_EDUCATIVA_PAGARE_CLAUSULA_INCUMPLIMIENTO`.

No existen textos predeterminados para esas variables. El modo educativo no usa
libranza, pagaduria ni nomina y aclara que no existe desembolso de dinero al
estudiante. Firmante, valores y fechas provienen exclusivamente de la solicitud
y su fotografia financiera activa y bloqueada. La version juridica forma parte
de `version_plantilla`; la version y el hash del PDF quedan persistidos y un
artefacto enviado no se modifica.

La ficha usa como fuente `FO-AD-005 V2`, identificada por el hash documentado
en `services/ficha_matricula.py`. El PDF recibido es plano, no contiene
AcroForm y trae datos personales de ejemplo incrustados; superponer campos lo
dejaria con PII residual. Por eso se aplica la tercera estrategia permitida:
una reproduccion de una pagina con la misma estructura de secciones. El mapeo
usa solo datos reales y marca como `No informado` telefono alterno, municipios,
renovacion, ocupacion y demas valores no disponibles. Esta pagina forma parte
del PDF firmado y tambien se conserva como artefacto independiente para
compatibilidad con las consultas existentes.

Antes de una firma real deben aprobarse juridicamente los tres textos
configurables, su version, la representacion del menor y los datos legales del
acreedor. La prueba
automatica valida estructura, fuente, ausencia de PII de la muestra, una sola
pagina, version, hash e idempotencia; la comparacion visual final debe aprobarse
con el area propietaria del formato.

## Documentacion y ejemplos

La coleccion reproducible se encuentra en
`docs/api-aliados/postman/aprobado-financiacion-educativa.postman_collection.json`.
El esquema OpenAPI versionado esta en `docs/api-aliados/openapi.yaml` y se
publica en `/api/v1/schema/`.

- Guia para reiniciar el entorno:
  `docs/interno/operaciones/REANUDAR_PROYECTO.md`.
- Contrato autocontenido para aliados:
  `docs/api-aliados/GUIA_INTEGRACION_API.md`.
- Guion manual con base y almacenamiento temporales:
  `docs/interno/operaciones/VALIDACION_FLUJO_EDUCATIVO.md`.
