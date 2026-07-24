# Contractors

Contexto acotado para el portal unico de contratistas de Aprobado.

## Definicion Actual De Negocio

`contractors` ya no se modela como multiples portales por empresa ni como una
linea generica separada. El portal publico correcto es:

- `contratistas.aprobado.com.co`
- en local, solo para desarrollo: `contratistas.localhost` cuando `DEBUG=True`

No deben existir experiencias publicas por empresa del tipo:

- `datain.aprobado.com.co`
- `acme.aprobado.com.co`
- `<slug>.aprobado.com.co`

El credito para contratistas debe evolucionar como credito por libranza /
adelanto con reglas propias de contratistas:

- score interno
- DataCredito futuro
- ley de libranza
- capacidad basada en contrato vigente
- valor pendiente por cobrar del contrato
- regla de 40% pagado para recogida de cartera
- pagador como responsable de pago, novedad de nomina u honorarios

El pagador no aprueba el credito. El pagador recibe la novedad y paga segun el
flujo operativo definido.

La empresa contratante/pagador no se captura como texto libre en el portal
publico. El contratista debe seleccionar una `Empresa` existente del core
`gestion_creditos`, con convenio activo y tipo compatible con libranza. El
portal de contratistas no crea empresas, pagadores ni convenios.

## Arquitectura Visual Publica

La landing publica principal vuelve a ser `/libranza/`. Contractors no tiene
landing publica duplicada ni se presenta como producto visual independiente.

El subdominio `contratistas.aprobado.com.co` se mantiene solo como entrada al
flujo de solicitud contratista:

1. `/` redirige a `/solicitar/`.
2. `/solicitar/` exige login/registro antes de mostrar el formulario.
3. El usuario registra datos personales y carga documentos obligatorios desde el
   inicio del formulario.
4. El usuario selecciona empresa existente y confirma informacion contractual.
5. La vista crea la pre-solicitud y redirige a la simulacion asociada.

El formulario, documentos y simulador reutilizan estilos de libranza, pero ya no
existe hero, FAQ, requisitos ni CTA final propios de contractors.

## Diagnostico Del Codigo Existente

Se conserva como valido:

- `ContractorApplication` como pre-solicitud aislada
- `ContractorApplicationDocument`
- servicios de creacion de solicitud
- servicios documentales
- revision documental
- validaciones de archivo
- admin interno
- evaluacion read-only de elegibilidad documental

Queda congelado o marcado como deuda:

- multiples subdominios por organizacion
- multiples landings por empresa
- simulador publico antes de registro
- `ContractorProductConfig` como producto independiente por tenant
- conversion directa a `Credito` sin logica de libranza, adelanto y pagador
- idea de `Credito.LineaCredito.CONTRATISTA`
- pagaré generico distinto sin ley de libranza

## Configuracion Semantica Del Portal

La fuente semantica nueva para el portal unico es:

- `ConfiguracionPortalContratistas`

Este modelo consolida lo que antes estaba repartido en:

- `ContractorOrganization`
- `ContractorBranding`
- `ContractorProductConfig`

Debe existir una sola configuracion activa por `host`. El host productivo
esperado es `contratistas.aprobado.com.co`. En local `contratistas.localhost`
funciona con `DEBUG=True`; si no existe una configuracion local exacta, el
middleware puede usar la configuracion activa con `slug=contratistas`.

`ContractorOrganization`, `ContractorBranding` y `ContractorProductConfig`
quedan como modelos legacy/congelados por compatibilidad. No se borran, no se
renombran y no deben usarse para nuevas vistas publicas.

`ContractorApplication` ahora puede apuntar a `configuracion_portal`. La FK
legacy `organization` queda nullable para permitir pre-solicitudes nuevas sin
depender conceptualmente de organizaciones por subdominio.

Si se necesita conservar compatibilidad con solicitudes antiguas, una
`ContractorOrganization` heredada puede seguir existiendo con:

- `slug = contratistas`
- `subdomain = contratistas`
- `is_active = True`

Renombrar o borrar modelos legacy no se hace todavia para evitar migraciones
ruidosas y riesgo sobre datos existentes.

## Rutas Publicas

Rutas actuales bajo `contratistas.aprobado.com.co`:

- `/`: redirige a `/solicitar/`
- `/login/`: acceso personalizado reutilizando la experiencia de libranza
- `/registro/`: registro personalizado reutilizando la experiencia de libranza
- `/solicitar/`: crea pre-solicitud controlada con documentos iniciales
- `/solicitud/<solicitud_id>/documentos/`: carga versiones adicionales o reemplazos documentales
- `/simular/?solicitud_id=<id>`: simulacion asociada a una pre-solicitud existente
- `/mi-credito/`: redirige al panel existente de libranza/personas

El dominio raiz `aprobado.com.co` no debe exponer rutas de contractors.

`/simular/` ya no debe usarse como entrada publica directa. Si no existe una
pre-solicitud asociada, la vista redirige a `/solicitar/`.

## Flujo Objetivo

Flujo correcto:

1. Login/registro.
2. Registro de pre-solicitud contratista con datos personales.
3. Carga temprana de documentos obligatorios.
4. Seleccion de empresa de convenio existente y datos contractuales.
5. Simulacion asociada a la pre-solicitud.
6. Validacion documental interna.
7. Evaluacion de capacidad contractual.
8. Score interno.
9. DataCredito futuro.
10. Evaluacion de ley de libranza / adelanto.
11. Aprobacion interna.
12. Pagare contratista/libranza.
13. Firma por ZapSign con validacion de identidad obligatoria.
14. Notificacion de novedad al pagador.
15. Pendiente transferencia.
16. Desembolso / activacion.

No esta implementado todavia:

- score completo
- DataCredito
- pagaré contratista/libranza
- ZapSign con validacion de identidad obligatoria
- conversion a `Credito`
- notificacion a pagador
- transferencia
- activacion

## Datos Contractuales

Ya existe soporte interno para registrar datos laborales/contractuales asociados
a una pre-solicitud:

- cargo
- tipo de contrato
- fecha de inicio del contrato
- fecha de fin del contrato
- valor total del contrato
- valor pagado del contrato
- valor pendiente por cobrar
- empresa contratante seleccionada desde `gestion_creditos.Empresa`

Modelo actual:

- `InformacionLaboralSolicitudContratista`

Servicio actual:

- `registrar_datos_contractuales_contratista`
- `calcular_valor_pendiente_contrato`
- `evaluar_capacidad_contractual_contratista`

Selectors actuales:

- `obtener_datos_contractuales_solicitud`
- `solicitud_tiene_datos_contractuales`

Estos datos son base para capacidad, ley de libranza y validacion de recogida
de cartera. Todavia no estan conectados a score, DataCredito, decision
productiva, notificacion a pagador ni conversion a `Credito`.

Los campos legacy `empresa_contratante_nombre`, `empresa_contratante_nit`,
`pagador_nombre`, `pagador_email` y `pagador_telefono` se conservan para
compatibilidad historica, pero no se piden al usuario en la UI publica nueva.
La fuente operativa es la FK `empresa`.

## Capacidad Contractual

Ya existe evaluacion read-only de capacidad contractual:

- servicio `evaluar_capacidad_contractual_contratista`
- DTO `ResultadoCapacidadContractualContratista`
- helper `calcular_meses_restantes_contrato`

Reglas actuales:

- la pre-solicitud debe tener datos contractuales/laborales
- el contrato no debe estar vencido
- `valor_pendiente_cobrar` debe ser mayor a cero
- `requested_amount` no debe superar `valor_pendiente_cobrar`
- `term_months` no debe exceder los meses restantes del contrato
- la capacidad maxima estimada actual es el valor pendiente por cobrar

Esta evaluacion no usa DataCredito, no usa score, no crea credito, no modifica
estados y no notifica al pagador. La integracion con ley de libranza productiva
y capacidad completa queda pendiente para una fase posterior, cuando existan
datos suficientes de ingreso/honorarios, cuota proyectada y reglas finales.

## Predecision Consolidada Read-Only

Existe un servicio de predecision consolidada que combina el estado de una
pre-solicitud antes de cualquier decision productiva:

- servicio `evaluar_predecision_contratista`
- DTO `ResultadoPredecisionPrestador`
- alias legacy `ResultadoPredecisionContratista`

La predecision evalua:

- elegibilidad documental con `evaluar_elegibilidad_conversion_contratista`
- capacidad contractual con `evaluar_capacidad_contractual_contratista`
- riesgo con credito previo localizado por documento del solicitante
- el escenario solicitado en `ContractorApplication.escenario_credito`
- segundo credito usando `risk.services.second_credit` solo si el escenario es `SEGUNDO_CREDITO`
- recogida de cartera usando `risk.services.portfolio_takeover` solo si el escenario es `RECOGIDA_CARTERA`
- score interno read-only con `contractors.score`
- DataCredito como `PENDIENTE`

La salida consolidada incluye:

- `eligible`
- `decision`
- `razones`
- `bloqueos`
- `advertencias`
- `escenario_credito`
- `documental_status`
- `capacidad_status`
- `riesgo_status`
- `datacredito_status`
- `score_status`
- `score_resultado`
- `datacredito_resultado`
- `capacidad_resultado`
- `segundo_credito_resultado`
- `recogida_cartera_resultado`
- `monto_maximo_sugerido`
- `plazo_maximo_sugerido`
- `requiere_revision_manual`
- `fuente=predecision_prestadores_read_only`

Decisiones posibles:

- `PREAPROBADO_READ_ONLY`: documental, capacidad, riesgo, DataCredito y score no tienen bloqueos.
- `REQUIERE_REVISION_MANUAL`: no hay bloqueo critico, pero falta DataCredito o el score requiere revision.
- `BLOQUEADO_READ_ONLY`: capacidad, riesgo o DataCredito tienen bloqueo critico.
- `INCOMPLETO`: faltan documentos criticos o informacion documental requerida.

Escenarios actuales:

- `NUEVO_CREDITO`: aplica cuando no hay credito previo o no se pretende usar uno.
- `SEGUNDO_CREDITO`: el credito anterior sigue activo y el contratista solicita otro credito adicional.
- `RECOGIDA_CARTERA`: el nuevo credito recoge/paga el saldo anterior y calcula desembolso neto.

Reglas actuales:

- si falla documental, la predecision no es elegible
- si falla capacidad contractual, la predecision no es elegible
- si falla documental, la decision queda `INCOMPLETO` y no se evalua score
- si falla capacidad contractual, la decision queda `BLOQUEADO_READ_ONLY`
- en `NUEVO_CREDITO`, si no existe credito previo, riesgo no bloquea
- en `NUEVO_CREDITO`, si existe credito previo, bloquea con `credito_previo_existente_requiere_escenario`
- en `SEGUNDO_CREDITO` y `RECOGIDA_CARTERA`, si no existe credito previo, bloquea con `no_existe_credito_previo`
- en `SEGUNDO_CREDITO`, solo se evalua segundo credito: minimo 40% pagado, sin mora y capacidad si la regla aplica
- en `RECOGIDA_CARTERA`, solo se evalua recogida de cartera: minimo 40% pagado, sin mora, saldo pendiente y desembolso neto
- si DataCredito trae mora severa, la decision queda `BLOQUEADO_READ_ONLY`
- si DataCredito no esta disponible, la decision queda `REQUIERE_REVISION_MANUAL`
- si score queda en banda `REVISION`, la decision queda `REQUIERE_REVISION_MANUAL`
- score y DataCredito no aprueban ni rechazan productivamente
- el score solo se evalua si documental, capacidad contractual y riesgo no tienen bloqueos
- si DataCredito esta pendiente, el score puede calcularse parcialmente y exige revision manual

Montos y plazos sugeridos:

- `monto_maximo_sugerido` es el minimo entre monto por banda de score,
  capacidad contractual disponible y configuracion del portal.
- `plazo_maximo_sugerido` es el minimo entre plazo por banda de score, meses
  restantes del contrato y configuracion del portal.

La predecision no cambia estados, no crea `Credito`, no crea pagaré, no crea
historiales, no notifica pagador y no dispara workflows.

## Pagador

El pagador debe recibir notificacion cuando el credito sea aprobado y exista una
novedad operativa. Si una empresa tiene activador y pagador, las notificaciones
y resumenes deben llegar a ambos roles.

Pendiente de implementar:

- servicio de notificacion a pagador
- reglas de destinatarios activador/pagador
- trazabilidad de novedad
- vistas documentales para pagador

## Documentos Visibles Para Pagador

Debe diseñarse un modulo documental en la vista de pagador para consultar
documentos del colaborador o contratista cuando aplique.

El pagador puede ver documentos operativos del colaborador/contratista.

El pagador no debe ver:

- pagaré firmado
- evidencias de transferencia
- informacion financiera interna sensible no necesaria para la novedad

Esta regla aplica tanto a contratistas como a libranza.

## Pagaré

El archivo `Contrato de Mutuo y Autorización Irrevocable.docx` sera base de la
plantilla futura del pagaré contratista/libranza.

La plantilla debe conservar exactamente la informacion legal del documento. Se
pueden adaptar estilos, logos y colores de Aprobado sin alterar obligaciones,
autorizaciones ni contenido legal sustantivo.

La firma debe usar ZapSign con validacion de identidad obligatoria. La
generacion del pagaré no esta implementada en esta fase.

## Score Interno Read-Only

Existe un motor interno read-only para prestadores de servicios en:

- `contractors/score/configuracion.py`
- `contractors/score/dto.py`
- `contractors/score/policies.py`
- `contractors/score/motor.py`

El motor usa `CONFIGURACION_SCORE_PRESTADORES_V1`, que separa la configuracion
de la logica:

- pesos por componente
- bandas de score
- montos y plazos sugeridos
- penalizaciones
- reglas criticas

Componentes actuales:

- DataCredito: se consulta mediante adapter read-only; por defecto queda `PENDIENTE`/`no_configurado`.
- Capacidad: viene de la evaluacion contractual.
- Comportamiento digital: usa valor default configurable.
- Riesgo fraude: usa valor default configurable.
- Referencias: usa valor default configurable.
- Geolocalizacion: no suma; solo penaliza si hay dato y cae bajo umbral.

El resultado incluye:

- version de configuracion
- score final entre 0 y 1000
- banda
- decision preliminar read-only
- monto y plazo sugeridos
- componentes evaluados
- componentes pendientes
- penalizaciones
- razones
- `requiere_revision_manual`
- `datacredito_status`

La predecision consulta primero el adapter DataCredito read-only. Si el
resultado esta disponible, el score usa `score_normalizado_0_1000` como
componente `datacredito`. Si DataCredito esta pendiente, el score queda parcial
y read-only. Si aparece mora severa, la predecision agrega un bloqueo
read-only y no evalua score.

Pendiente:

- persistir historico de resultados;
- administrar parametros desde Django Admin;
- conectar DataCredito real;
- calibrar pesos y bandas contra el Excel `simulador_fintech_aprobado.xlsx`;
- definir trazabilidad de cambios de configuracion.

## Adapter DataCredito Read-Only

Existe una capa desacoplada en `contractors/datacredito/`:

- `dto.py`
- `adapter.py`
- `mock.py`
- `normalizador.py`
- `README.md`

Objetivo:

- trabajar con escenarios controlados ahora;
- conectar proveedor real despues sin acoplar score ni predecision al proveedor;
- retornar solo resultados sanitizados;
- no guardar respuestas crudas, XML, JSON completo ni documento completo.

Settings:

- `CONTRACTORS_DATACREDITO_ENABLED=False`
- `CONTRACTORS_DATACREDITO_PROVIDER=mock`
- `CONTRACTORS_DATACREDITO_TIMEOUT_SECONDS=10`
- `CONTRACTORS_DATACREDITO_MOCK_SCENARIO=bueno`

Por defecto no se consulta nada y el resultado queda:

- `disponible=False`
- `fuente=no_configurado`
- `score_normalizado_0_1000=None`
- `requiere_revision_manual=True`

Escenarios mock disponibles:

- `bueno`
- `medio`
- `mora_severa`
- `no_disponible`

El proveedor `real` queda reservado y responde `proveedor_real_no_implementado`
hasta tener contrato tecnico confirmado. No hay consulta productiva real en esta
fase.

## API Principal Conservada

- `obtener_organizacion_por_subdominio`
- `obtener_configuracion_portal_contratistas_por_host`
- `obtener_configuracion_portal_contratistas_por_slug`
- `obtener_configuracion_producto_activa`
- `obtener_branding_activo_por_organizacion`
- `obtener_contexto_branding_con_defaults`
- `obtener_perfil_contratista_usuario`
- `usuario_pertenece_a_organizacion`
- `obtener_solicitud_contratista`
- `listar_solicitudes_por_organizacion`
- `listar_documentos_solicitud_contratista`
- `solicitud_tiene_documento_tipo`
- `obtener_ultimo_documento_por_tipo`
- `simular_credito_contratista`
- `simular_credito_portal_contratistas`
- `crear_solicitud_contratista`
- `registrar_documento_solicitud_contratista`
- `marcar_solicitud_en_revision`
- `rechazar_solicitud_contratista`
- `aprobar_documento_solicitud`
- `rechazar_documento_solicitud`
- `evaluar_elegibilidad_conversion_contratista`
- `obtener_datos_contractuales_solicitud`
- `solicitud_tiene_datos_contractuales`
- `listar_empresas_libranza_convenio_activas`
- `obtener_credito_previo_por_documento_solicitud`
- `registrar_datos_contractuales_contratista`
- `calcular_valor_pendiente_contrato`
- `evaluar_capacidad_contractual_contratista`
- `calcular_meses_restantes_contrato`
- `evaluar_predecision_contratista`
- `evaluar_score_interno_prestador`
- `consultar_datacredito_prestador`
- `DatosSolicitudContratista`
- `ResultadoSolicitudContratista`
- `DatosDocumentoSolicitudContratista`
- `ResultadoDocumentoSolicitudContratista`
- `DatosContractualesContratista`
- `ResultadoDatosContractualesContratista`
- `ResultadoCapacidadContractualContratista`
- `ResultadoPredecisionContratista`
- `ResultadoElegibilidadConversionContratista`
- `ResultadoSimulacionCreditoContratista`
- `ErrorSimulacionContratista`

Se mantienen aliases temporales en ingles para compatibilidad con imports
existentes durante la transicion.

## Fuera De Alcance En Esta Fase

- No se implementa score productivo ni aprobacion automatica.
- No se implementa DataCredito real/productivo.
- No se implementa pagaré.
- No se implementa ZapSign.
- No se implementa conversion a `Credito`.
- No se toca pagos.
- No se toca WhatsApp.
- No se toca el flujo productivo de libranza.
- No se introduce `Credito.LineaCredito.CONTRATISTA`.
- No se crea motor generico.

## Validacion Operativa Local

Para probar el portal unico en local:

```powershell
$env:DEBUG="True"
$env:CONTRACTORS_PORTAL_HOST="contratistas.localhost"
$env:ALLOWED_HOSTS="localhost,127.0.0.1,.localhost"
venv\Scripts\python.exe manage.py migrate contractors
venv\Scripts\python.exe manage.py contractors_demo_data --host contratistas.localhost
venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000
```

El comando `contractors_demo_data` tambien crea o actualiza una empresa de
convenio llamada `Empresa Convenio Contratistas Demo`, necesaria para diligenciar
el formulario publico.

Tambien puede crearse la configuracion demo manualmente:

```python
from decimal import Decimal
from contractors.models import ConfiguracionPortalContratistas

ConfiguracionPortalContratistas.objects.update_or_create(
    host='contratistas.aprobado.com.co',
    defaults={
        'nombre_visible': 'Portal Contratistas',
        'slug': 'contratistas',
        'activo': True,
        'color_primario': '#0d6efd',
        'color_secundario': '#6c757d',
        'texto_landing': 'Credito para contratistas Aprobado.',
        'monto_minimo': Decimal('1000000.00'),
        'monto_maximo': Decimal('10000000.00'),
        'plazo_minimo_meses': 3,
        'plazo_maximo_meses': 24,
        'tasa_mensual': Decimal('2.5000'),
        'tasa_comision': Decimal('5.0000'),
        'comision_fija': Decimal('0.00'),
        'tasa_iva': Decimal('19.0000'),
    },
)
```

URLs locales:

- `http://contratistas.localhost:8000/`
- `http://contratistas.localhost:8000/login/?next=/solicitar/`
- `http://contratistas.localhost:8000/solicitar/`
- `http://contratistas.localhost:8000/solicitud/<id>/documentos/`
- `http://contratistas.localhost:8000/simular/?solicitud_id=<id>`
- `http://contratistas.localhost:8000/terminos-y-condiciones/`
- `http://contratistas.localhost:8000/politica-de-privacidad/`

Flujo manual recomendado:

1. Abrir `http://contratistas.localhost:8000/`; el portal redirige a `/solicitar/`.
2. Si el usuario no esta autenticado, entra primero por login/registro.
3. Diligenciar datos personales.
4. Cargar documentos iniciales:
   - Cedula frontal y reversa: captura obligatoria desde camara.
   - Contrato actual y certificado bancario: PDF obligatorio.
   - Nota visible al usuario: la cedula se captura en vivo desde el dispositivo y el certificado bancario se carga en PDF para validacion.
5. Buscar empresa por autocomplete y seleccionar una empresa existente con convenio activo.
6. Confirmar condiciones solicitadas y datos contractuales.
7. Aceptar terminos y privacidad con los enlaces del portal.
8. La vista registra la pre-solicitud y los cuatro documentos iniciales.
9. El flujo redirige a `/simular/?solicitud_id=<id>`.
10. `/solicitud/<id>/documentos/` queda disponible para versiones adicionales o reemplazos.
11. Revisar internamente desde Django Admin si aplica.

## UX Y Validaciones Publicas

El portal publico reutiliza la experiencia visual de libranza:

- Navbar y footer Aprobado.
- No existe landing publica propia, hero, cards, FAQ ni CTA final de contractors.
- Formulario con `form-container`, sidebar, stepper, `form-input`, `form-select`, errores y botones del flujo de libranza.
- El stepper publico tiene cuatro pasos reales: informacion personal, documentos, empresa contratante e informacion contractual/confirmacion.
- La aceptacion de terminos y privacidad esta al final del ultimo paso real, no en un paso independiente.
- Documentos obligatorios dentro del flujo inicial: cedula frontal, cedula trasera, contrato vigente PDF y certificado bancario PDF.
- Simulador con estructura visual `simulador-*`.
- Footer minimo con logo Aprobado, texto institucional breve, contacto e iconos
  de WhatsApp, Facebook e Instagram. No incluye pagadores, terminos, privacidad
  ni columnas de enlaces.

Reglas implementadas en esta fase:

- `/solicitar/`, `/solicitud/<id>/documentos/` y `/simular/` requieren autenticacion.
- `/mi-credito/` reutiliza el panel existente de libranza/personas mediante redireccion.
- Los CTAs publicos redirigen a `/login/?next=/solicitar/`.
- La empresa contratante se selecciona desde resultados de busqueda; no se digita pagador manual.
- Si el usuario escribe una empresa pero no selecciona resultado, se muestra `Debes elegir una empresa de la lista de resultados.`
- Nombres, apellidos, cedula, celular, correo y direccion tienen validaciones basicas de calidad.
- Tipo de documento es select cerrado: `CC` para cedula de ciudadania y `CE` para cedula de extranjeria.
- Numero de documento acepta solo numeros de 6 a 10 digitos y rechaza secuencias evidentemente invalidas.
- Contrato vigente y certificado bancario solo aceptan PDF en el formulario inicial.
- Cedula frontal y trasera no permiten carga manual desde galeria; deben capturarse en vivo desde camara.
- No se permite reutilizar el mismo archivo para diferentes documentos de la misma solicitud; en backend se compara nombre, tamano, tipo y hash temporal.
- Los documentos no exponen `file.path`.

Pendiente antes de produccion completa:

- Confirmacion automatica de datos extraidos.
- Textos legales definitivos de terminos y politica de privacidad.
- Score interno, DataCredito, pagare, ZapSign, conversion a credito y notificaciones al pagador.

## Analisis Inicial De Contrato Con IA

Existe el servicio `contractors.services.analisis_contrato_ia.analizar_contrato_con_openai`.

La integracion es controlada por variables de entorno:

- `OPENAI_API_KEY`
- `CONTRACTORS_CONTRACT_AI_ENABLED=True/False`
- `CONTRACTORS_CONTRACT_AI_MODEL`

Ejemplo `.env`:

```env
CONTRACTORS_CONTRACT_AI_ENABLED=True
CONTRACTORS_CONTRACT_AI_MODEL=gpt-4.1-mini
OPENAI_API_KEY=...
```

Reglas actuales:

- Se usa OpenAI Responses API.
- Cuando `CONTRACTORS_CONTRACT_AI_ENABLED=True`, se envia el PDF del contrato como archivo de entrada.
- El modelo se configura con `CONTRACTORS_CONTRACT_AI_MODEL`.
- La API key se toma de `OPENAI_API_KEY` y nunca debe imprimirse ni exponerse en UI.
- Se solicita salida estructurada en JSON.
- Si la IA esta deshabilitada o falta `OPENAI_API_KEY`, el formulario no se rompe y la informacion contractual se diligencia manualmente.
- No se ejecuta OCR propio ni parser PDF fragil.
- No se guardan prompts completos ni contenido completo del contrato en logs.
- La IA no decide aprobacion; solo extrae informacion para confirmacion del usuario.
- Si la IA detecta que el PDF no parece ser contrato, el formulario bloquea la solicitud con un mensaje claro.
- La respuesta de IA nunca se toma como verdad absoluta; queda marcada para confirmacion del usuario.
- Se guarda metadata segura dentro de `simulation_payload['analisis_contrato_ia']`: `enabled`, `attempted`, `success`, `modelo`, `es_contrato`, `campos_detectados`, `campos_no_encontrados`, `advertencias`, `confianza_general`, `requiere_confirmacion_usuario`, `error_tipo` y `documento_id` si ya existe.
- La metadata no guarda prompt completo, texto completo del contrato, base64 del PDF ni API key.
- Si se compartio una API key real fuera del entorno privado, debe rotarse antes de produccion.

## Autocompletado Contractual Con IA

El formulario de `/solicitar/` incluye un analisis asistido del contrato. El
usuario carga el contrato PDF, pulsa `Analizar contrato` y el portal llama el
endpoint autenticado:

- `POST /contrato/analizar/`

Reglas del endpoint:

- requiere login y CSRF;
- requiere autorizacion explicita de tratamiento de datos antes de llamar OpenAI;
- recibe un PDF temporal;
- valida extension, `content_type` y tamano maximo;
- aplica limite basico de llamadas repetidas por usuario/IP usando cache;
- llama `analizar_contrato_con_openai`;
- devuelve JSON seguro para autocompletar el formulario;
- no crea `ContractorApplication`;
- no crea `Credito` ni `CreditoLibranza`;
- no guarda archivo permanente;
- no guarda prompt, base64, texto completo del contrato ni API key.

Campos sugeridos por IA:

- empresa contratante y NIT como referencia;
- nombre/documento del contratista como advertencia de consistencia;
- cargo o servicio;
- fecha de inicio y fin del contrato;
- valor total;
- valor mensual u honorarios como referencia;
- valor pendiente estimado;
- moneda.

La empresa detectada no crea una `Empresa` nueva ni reemplaza automaticamente
la seleccion operativa. Solo se usa como sugerencia de busqueda; el usuario debe
seleccionar una empresa existente del core de libranza.

La respuesta de IA nunca aprueba credito ni se toma como verdad absoluta. El
usuario debe confirmar o corregir los campos antes de enviar. Al crear la
pre-solicitud se guardan los datos confirmados por el usuario y solo metadata
segura de IA:

- `attempted`
- `success`
- `modelo`
- `confianza_general`
- `campos_detectados`
- `campos_no_encontrados`
- `advertencias`
- `requiere_confirmacion_usuario`
- `error_tipo`

Si `CONTRACTORS_CONTRACT_AI_ENABLED=False`, falta `OPENAI_API_KEY` o OpenAI
falla, el endpoint responde con `manual_allowed=true` y el usuario puede
continuar diligenciando manualmente. Si la IA confirma que el PDF no parece un
contrato, el avance queda bloqueado hasta cargar un contrato valido.

Antes de enviar el PDF a OpenAI, el usuario debe aceptar la autorizacion de
tratamiento de datos del analisis contractual. Si no la acepta, el endpoint no
llama OpenAI y responde:

`Debes aceptar la autorizacion de tratamiento de datos antes de analizar el contrato.`

Usuario admin:

```powershell
venv\Scripts\python.exe manage.py createsuperuser
```

Variables locales minimas:

- `DEBUG=True`
- `CONTRACTORS_PORTAL_HOST=contratistas.localhost`
- `ALLOWED_HOSTS` debe incluir `.localhost`, `contratistas.localhost` o `*`

Evitar comentarios inline en `.env` para valores usados por Django. Por ejemplo,
usar `PRIMARY_DOMAIN_HOST=localhost`, no `PRIMARY_DOMAIN_HOST=localhost # comentario`.
