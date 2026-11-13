# Roadmap de financiacion educativa

Actualizado: 2026-11-13. Producto independiente de libranza y gestion_creditos.
El backlog legado se conserva en `archivo/legacy/` y no gobierna este roadmap.

## Estado y criterios de cierre

| Bloque | Estado | Criterio de cierre |
| --- | --- | --- |
| Firma, webhook y API | Cierre verificado en produccion para los dos casos Jowalth | JOW-4FE29332-PROD y JOW-88C00418-PROD devuelven APPROVED, course_authorized=true, fecha de autorizacion y financial_terms. Las fechas de firma del proceso y del artefacto coinciden. Verificacion operativa confirmada por Carlos; no constituye certificacion de otros casos. |
| Dashboard financiero inicial | Desplegado y comprobado en produccion por Carlos | Indicadores y listado reconcilian, solo cuentan contratos realmente autorizados, excluyen simulaciones y muestran el calendario firmado. |
| Fase 2A: reportes BRE-B y conciliacion | Carlos confirma despliegue 300bf42 e infraestructura instalada; canal productivo deshabilitado | Base local coincide con ese SHA. Activacion pendiente de datos bancarios oficiales, permisos, diagnostico y certificacion del ambiente; no se accedio al servidor. El ciclo sintetico anterior no acredita una transferencia real. |
| Soporte de ingresos configurable | Carlos confirma soporte opcional activo en Jowalth | Confirmacion operativa recibida; no se modifico configuracion ni se consulto el servidor en esta revision. Las demas instituciones conservan su politica. |
| Experiencia de recaudos y conciliacion | Incluida en la base desplegada dd09e8c; antecedente inicial 300bf42 | Acceso desde finanzas, QR/soportes SAFE e integridad verificada antes de servir/activar; rotacion exige recarga sin cambiar QR silenciosamente. Confirmacion bancaria pendiente de aplicacion, no cuota pagada. No se infiere la configuracion productiva actual desde un merge. |
| Fase 2B: libro de aplicaciones | Alcance manual incluido en la base desplegada; registro/aplicacion y consulta del estudiante comprobados previamente en staging por Carlos | Reutiliza el ingreso conciliado para cubrir exactamente la primera cuota pendiente, con verificacion explicita del operador, vista previa firmada, auditoria, bloqueo y unicidad. No altera calendario firmado. Parciales, excedentes, abonos, reversos, liquidaciones y mora siguen pendientes. |
| Distribucion financiera | Pendiente de libro 2B y convenio confirmado | Originacion 10% del monto solicitado ya calculada por motor/fotografia. Reparto propuesto de intereses 80% APROBADO y 20% Jowalth sin confirmar ni implementar. Capital, IVA, garantia y seguro separados; no repartir el ingreso bruto ni inventar reglas. |
| Dashboard de recaudos y aplicaciones (2026-11-12) | PR #7 integrado en 7993980 y desplegado, segun confirmacion de Carlos | Confirmados, pendientes de aplicar y aplicados por institucion y fecha efectiva bancaria, con capital/interes solo de aplicaciones persistidas. Resumen mensual y listados de respaldo comparten cohorte. No agrega reparto, parciales ni saldos a corte. Ver `TABLERO_RECAUDOS_APLICACIONES.md`. |
| Notificaciones de pagos BRE-B | PR #8 integrado en dd09e8c; avisos activos en produccion segun confirmacion de Carlos del 2026-11-13; primer envio real pendiente de verificar | Reporte recibido, ingreso confirmado y pago aplicado en el outbox transaccional. Menores: tutor verificado como principal. Sin copia institucional automatica; auditoria posterior a SENT segun configuracion. Avisos superados no se envian. SENT no acredita entrega en buzon ni distingue por si solo una prueba de un primer envio real. No se cambia el flag en esta entrega. Ver `NOTIFICACIONES_PAGOS_BREB.md`. |
| Portal de Operaciones: incremento de Recaudos (2026-11-12) | Integrado por PR #4 e incluido en la base desplegada dd09e8c | Bandejas Por verificar / Por aplicar / Aplicados derivadas de los registros existentes. Registro y conciliacion conservan los datos al cargar, consultar y abrir evidencia propia. No introduce estados, permisos, migraciones ni reglas de reparto. Evidencia y validacion: `PORTAL_OPERACIONES_RECAUDOS.md`. |
| Reportes institucionales (2026-11-12) | PR #6 cerrado y desplegado, segun confirmacion de Carlos; merge c7ed9b7 presente en origin/main | Cohorte unica por radicacion y curso: recibidas, aprobadas, cursos con autorizacion efectiva y aprobadas pendientes de autorizacion. Grafica mensual y listados enlazados usan los mismos predicados y alcance tenant. Sin reparto, saldos, mora ni cambios de firma. Definiciones, capturas y pasos: `REPORTES_INSTITUCIONALES.md`. |
| Descarga de plan contractual PDF (2026-11-12) | Integrada en origin/main mediante bbddc11, merge del PR #9; despliegue no comprobado en esta entrega | Descarga privada desde el portal del titular, version fijada en URL, fotografia contractual autorizada y calendario persistido completos. Sin recalculo, saldo vigente, liquidacion, mutacion del contrato ni correo. Ver `PLAN_CONTRACTUAL_PDF.md`. |
| Seguimiento operativo de cartera (2026-11-12) | PR #10 integrado en f0f345d y desplegado en produccion, segun confirmacion de Carlos | Vencidas sin aplicacion, primeras cuotas vencidas, vence hoy, proximos 7/30 dias y aplicaciones persistidas. Cruce por obligacion/version con ingreso por aplicar, reporte por verificar o sin recaudo abierto; resumen sin multiplicar ingresos por cuotas. Portal del estudiante distingue vencida/hoy/proxima y movimientos abiertos no asignados. Solo lectura; no activa correo ni calcula mora/saldo/reparto. Ver `SEGUIMIENTO_CARTERA_EDUCATIVA.md`. |
| Libro 2B, parciales, abonos y liquidacion | PR #11 integrado en 7fc3773 y desplegado en produccion, segun confirmacion de Carlos; contabilizacion desactivada | Libro aditivo e inmutable, aplicaciones integras virtuales sin backfill y netos conciliables. Piloto y politica productiva pendientes. No activa mora, reparto ni notificaciones. No constituye autorizacion para operar pagos reales. Base y decisiones: `LIBRO_PAGOS_2B.md`. |
| Gobierno y piloto controlado del Libro 2B | PR #12 integrado en 58092ed y desplegado, segun Carlos; Libro 2B cerrado | No se activa contabilizacion, no se cargan politicas y no se comprueba configuracion remota en esta entrega. Base de gobierno y migracion 0035 preservadas. Evidencia historica del PR #12: PostgreSQL 16.13 aislado, 210/210 OK y cierre 68/68 OK, conjuntos solapados. Politica productiva y piloto siguen sujetos al procedimiento `PILOTO_LIBRO_2B.md`. |
| Envio del plan PDF por correo | Pendiente; separado de la descarga privada | La descarga contractual se implementa en este incremento. No se incorpora envio ni evento de correo. Definir destinatario/momento y entrega idempotente por outbox antes de implementarlo; no presentar el contrato como saldo actualizado. |
| Fase 2C: ePayco | Pendiente, sin adaptadores ni integracion activados | Comercio y credenciales sandbox autorizados; confirmacion servidor a servidor autenticada, importe/moneda/orden, idempotencia, ambiente y reversos. Nunca confirmar por la redireccion del navegador. |
| Saldos y causacion de intereses | Pendiente de pagos | Saldo real y capital/intereses aplicados derivan del libro de pagos conciliados; calculos reproducibles a fecha de corte y separados del calendario programado. |
| Mora | Pendiente de politica aprobada | Aprobar regla, tasas, base y fechas; implementar y probar sobre pagos conciliados. El dashboard inicial no calcula cargos por mora. |
| Abonos y pago total | Motor y vistas previas incluidos en PR #11; piloto y reglas productivas pendientes | Intereses a la fecha, reduccion de plazo manteniendo cuota y proyecciones trazables sin alterar el contrato original. El libro no declara cierre legal ni paz y salvo; intereses anticipados, vencimientos y residuales incompatibles derivan a revision. |
| Experiencia movil y revision humana | Implementada; verificacion de dispositivos pendiente | Probar Safari/iPhone y Chrome/Android, captura frente/reverso y recuperacion, permisos y revision humana con trazabilidad. |
| E2E menor con tutor | Cubierto localmente; certificacion staging pendiente | Tutor como unico firmante, evidencias y consentimientos correctos, firma/webhook/API completos e idempotentes. |
| Accesos individuales y notificaciones institucionales | Implementados; comprobacion operativa pendiente | Membresias explicitas y activas, aislamiento, cuentas individuales y matriz de destinatarios/outbox verificada. |
| Respaldos, restauracion y alertas | Candidato local del 2026-11-13; instalacion y recepcion de alertas en servidor pendientes | Ensayo sintetico pg_dump/pg_restore entre PostgreSQL Docker aislados, archivo privado verificable y app restaurada en localhost. Diagnostico agregado READ ONLY de servicios, outbox y firmas; sin recuperacion automatica. Procedimiento, umbrales, responsables y evidencia: `RESPALDO_RESTAURACION_ALERTAS_EDUCATIVAS.md`. No certifica un backup ni un desastre real de produccion. |
| Integracion y jerarquia Institucion -> Programa -> Curso | API implementada; jerarquia pendiente | Certificar integracion y replay; disenar y migrar jerarquia explicita sin inferir relaciones a partir de nombres academicos. |

## Politica financiera aprobada

### Soporte de ingresos institucional (2026-10-19)

Obligatorio por defecto. Se incorpora el ajuste persistente
`Institucion.requiere_soporte_ingresos`, administrable, con migracion
`instituciones.0003_institucion_requiere_soporte_ingresos`. Ninguna institucion
se configura automaticamente como opcional. La ausencia opcional permite envio,
revision y automatizacion; cualquier archivo aportado conserva sus controles.
Identidad, tutor, consentimiento, fotografia y firma mantienen sus reglas.
La politica efectiva queda trazada en el historial; contratos existentes no se
reinterpretan ni se reprocesan. Procedimiento para configurar Jowalth, verificar
y revertir: `docs/SOPORTE_INGRESOS_POR_INSTITUCION.md`. Pendiente revision local
y posterior certificacion de configuracion/terminos en staging.
Validacion focalizada local: 88 pruebas OK en 124.953 segundos, cero omisiones,
SQLite en memoria y proveedores falsos, incluido avance automatico a firma y
webhook simulado. Check, comprobacion de migraciones y diff --check correctos.

### Parametros financieros

COP, metodo frances, tasa mensual 1%, originacion 10% del monto solicitado, IVA de originacion 19%,
garantia 2%, seguro de vida 0,3711%. Los cargos y el calendario de cada contrato
se consultan desde su fotografia versionada, no se recalculan en el dashboard.
Pagos anticipados: intereses a la fecha y reduccion de plazo manteniendo cuota.
Para 2B se conserva causacion diaria base comercial 30 y vencimientos sin
desplazamiento por festivos. Antes de automatizar aplicaciones deben aprobarse
pagos tardios/parciales, excedentes, fechas retroactivas, correcciones y cierre.
No existe una politica de cargos por mora aprobada para este incremento.

El reparto 80% APROBADO / 20% Jowalth de intereses es una propuesta pendiente
de confirmar en el convenio. No modifica tasas, contratos ni calculos actuales.
Debe definirse su base (intereses efectivamente aplicados/recaudados o la que
apruebe el convenio), corte, participantes, redondeo y efecto de compensaciones.
No mezclarlo con originacion, capital, IVA, garantia o seguro ni implementarlo
como reparto del dinero bruto recibido.

Revision del 2026-11-12: no se encontro un convenio de reparto aprobado en el
repositorio ni fue suministrado para este incremento. La propuesta 80/20 no
define vigencia, criterio temporal ni redondeo; sigue bloqueada como calculo
definitivo. El alcance solicitado fija como unica base elegible el
`AplicacionCuotaEducativa.interes_aplicado`, no el ingreso confirmado pendiente.
No se habilitan importes Parte APROBADO / Parte institucion ni resumen de reparto
por periodo. Definiciones faltantes y PR financiero separado:
`PORTAL_OPERACIONES_RECAUDOS.md#reparto-de-intereses-bloqueado`.

## Dashboard inicial y verificacion

Rutas internas: `/financiacion-educativa/operaciones/obligaciones/` y su detalle
por UUID. Requieren los permisos existentes de acceso y consulta operativa.
Los filtros de obligaciones usan fechas de autorizacion efectiva, inclusivas en
la zona horaria de Django; los de tramite usan fechas de creacion. El proximo
vencimiento es la primera fecha contractual desde hoy, inclusive. El indicador
de 30 dias incluye hoy y el dia 30; no afirma que una cuota este impagada.

La elegibilidad reutiliza `firmas_autorizadas_reales` en `estado_publico.py`:
estado APPROVED, firma registrada, pagare firmado vigente con evidencia de
archivo/hash/tamano/fecha y fotografia activa, bloqueada, no legada, todos de
la misma solicitud. Los resultados Sandbox activos se excluyen de los importes
reales y de tramite. Un estado APPROVED sin evidencia coherente no es cartera.
La fecha registrada de firma del proceso debe coincidir con la del artefacto;
el webhook actual guarda ambas con el mismo instante. Los registros historicos
con fechas diferentes requieren comprobacion operativa y no se reparan al consultar.
Cada fotografia elegible se cuenta una vez, independientemente de relaciones
historicas. Las cifras se pueden abrir para consultar todos sus componentes.

El dashboard inicial no acredita pagos: capital pagado, intereses
recaudados, mora efectiva y saldo real figuran como no disponibles. Fase 2A agrega,
cuando se habilita, ingresos confirmados pendientes de aplicacion; tampoco son cuotas
pagadas ni capital amortizado. Una suma vacia de contratos elegibles si es cero;
eso no significa una cartera conciliada.

Secuencia: implementacion y pruebas locales -> revision y commit/push por Carlos
-> staging -> verificacion -> produccion.

En staging, Carlos debe contrastar los indicadores con solicitudes reales,
contratos sustituidos y simulaciones; abrir listado/detalle en movil y probar
permisos. El cierre firma/webhook/API de los dos casos Jowalth indicados ya fue
confirmado en produccion; esa comprobacion no sustituye la validacion del nuevo
dashboard en staging. No se hace ninguna llamada a proveedores ni escritura de
contratos al consultar estas pantallas.

### Validacion PostgreSQL local de Fase 1

El 2026-10-06 se ejecutaron exclusivamente estos modulos sobre PostgreSQL 16.13
temporal y aislado en Docker, sin cargar `.env` ni utilizar bases o credenciales
del servidor:

```text
financiacion_educativa.tests.test_dashboard_obligaciones
financiacion_educativa.tests.test_dashboard_operativo
financiacion_educativa.tests.test_api_institucional
financiacion_educativa.tests.test_firma_educativa
financiacion_educativa.tests.test_resultado_sandbox
```

Resultado: 87 pruebas en 145.571 segundos, OK, cero omisiones. La prueba
`ConcurrenciaSandboxTests.test_dos_operadores_generan_un_snapshot` se ejecuto y
paso sobre PostgreSQL real. No quedo omitida ninguna prueba concurrente de estos
modulos. Esta ejecucion no incluye otros modulos ni repite el QA visual.
La base de pruebas fue destruida por Django y el contenedor temporal se elimino
al finalizar. No hubo llamadas reales a proveedores, commit, push ni despliegue.


## Validacion de recaudos en staging: 2026-10-06

Evidencia operativa aportada por Carlos mediante salida del servidor y capturas:

- Release 317c2c8, migracion 0029 aplicada, check sin incidencias, estaticos
  recolectados y health HTTP 200. Servicios existentes reactivados.
- Unidad receipts-scan verificada con systemd-analyze en el VPS, instalada y
  activada tras configurar el canal. Acceso del usuario fundetec-staging al
  socket ClamAV comprobado mediante PONG.
- Receptor sintetico NO TRANSFERIR, sin cuenta ni QR bancario real, con imagen
  escaneada SAFE. Firma de staging conserva SIGNATURE_SEND_PAUSED=True.
- Caso QA E2E-APROBADO-20260925-001: reporte desde iPhone por COP 157738,
  comprobante SAFE/CLEAN, evidencia bancaria independiente y conciliacion con
  referencia QA-BANCO-20261006-001. Pantalla final: ingreso confirmado pendiente
  de aplicacion. No hubo transferencia bancaria real.
- La prueba manual no certifica todos los dispositivos ni lectura bancaria de
  un QR oficial. No se ha implementado aplicacion a capital/intereses.
- Produccion permanece en 57607cc con las obligaciones reales de Jowalth;
  este release BRE-B aun no se ha desplegado ni activado alli.

## Correccion inmediata: experiencia de recaudos

1. Conservar campos ante POST invalido y mostrar el error especifico; el mensaje
   generico y la redireccion actual impiden identificar el campo que fallo.
   Mantener funcionamiento sin JavaScript y explicar la reseleccion de archivos
   cuando el navegador no pueda conservarlos.
2. Seleccionar evidencia bancaria propia y SAFE desde la interfaz, sin copiar
   UUID. Permitir evidencia elegible existente; conservar validacion de acceso,
   finalidad, hash, antivirus y revalidacion al confirmar.
3. Precargar monto/fecha para verificacion; cuenta, actor y receptor se resuelven
   internamente. La referencia bancaria verificada conserva su obligatoriedad.
4. Observacion opcional en el caso sin diferencia de monto, con registro
   determinista de la accion; justificacion explicita obligatoria ante diferencia
   y motivo publico obligatorio al rechazar. Preservar replay e historicos.
5. Mejorar jerarquia, tarjetas, campos, botones, contraste, mensajes y carga de
   soportes en pagador y operador. Filtro institucional por nombre y seleccion,
   estados humanos y tabla contenida en movil. Trazas tecnicas en detalle.
6. Mantener la leyenda pendiente de aplicacion hasta implementar el libro real.
   Validar parsing COP y POST del navegador sin conversiones ambiguas ni floats.

## Fase 2B: cierre funcional del pago

Primer incremento: [aplicación manual de una cuota BRE-B](interno/operaciones/APLICACION_MANUAL_CUOTA_BREB.md), limitada a un ingreso conciliado que cubra íntegramente la primera cuota pendiente del calendario firmado. No incluye aplicación automática, parciales, abonos, reversos ni mora.


Propuesta del libro completo y decisiones pendientes: `docs/DISENO_RECAUDOS_FASE_2B.md`.
Estado actualizado 2026-11-11: Carlos confirma `82028d2` instalado en staging,
con 0031 y 0032 aplicadas. Confirma cuotas 1 y 2 pagadas en Operaciones,
ambos pagos aplicados visibles para el estudiante y acceso directo a evidencia
bancaria privada bloqueado con 403. Esta evidencia fue aportada por Carlos;
no constituye una inspeccion remota ejecutada por el agente.
Incluye ingreso recibido sin reporte previo del estudiante y aplicacion manual
integra, con permisos separados. Esta revision cierra localmente con datos
sinteticos la repeticion del registro, aplicacion unica y privacidad del resultado
del estudiante; no consulta ni opera el pago real. El procedimiento enlazado
distingue la evidencia local de la confirmacion remota aportada por Carlos.
PostgreSQL 16.13 local aislado: 39 pruebas OK, cero omisiones, cinco concurrencias.
Duplicados/reintentos e idempotencia: verificados localmente, NO verificados en
staging en este cierre. No atribuir ese resultado a la prueba manual del servidor.
La liberacion productiva y el uso del caso real requieren autorizaciones separadas.
Dashboard global
de saldos/cortes, reparto 80/20, notificaciones y envio de plan PDF siguen pendientes.
El detalle operativo muestra aplicaciones persistidas y capital restante segun
ellas; no es liquidacion a corte ni cambia financial_terms o APPROVED.

El objetivo aprobado es una accion de confirmar y aplicar el ingreso con
previsualizacion de su distribucion y resultado persistente. No basta cambiar
el texto de exito o descontar manualmente una cuota del calendario firmado.

Criterios del libro completo para fases posteriores (parciales, abonos,
liquidaciones, reversos y cierres NO implementados en este incremento):

- Una cuota cubierta completamente queda pagada; una parcialmente cubierta
  conserva el remanente. Disminuir cuotas pendientes solo al cubrirlas.
- Separar capital aplicado e intereses cobrados. El monto recibido completo no
  se resta del capital. Saldo real y calendario pendiente derivan del libro.
- Mantener los contratos y fotografias firmadas inmutables, junto con sus
  condiciones financieras publicas; almacenar aplicaciones y proyecciones aparte.
- Idempotencia y bloqueo de obligacion: un ingreso no puede aplicarse dos veces,
  incluso con doble clic, reintentos o operadores concurrentes.
- Abonos anticipados: intereses a la fecha, reduccion de plazo conservando cuota.
  Pago total: liquidacion reproducible y cierre coherente a la fecha efectiva.
- Preservar autorizacion del curso al cerrar financieramente; revisar el mapeo
  publico antes de reutilizar estados existentes. No reabrir matricula ni firma.
- Aplicar ingresos 2A previos solo mediante procedimiento explicito, acotado e
  idempotente. Correcciones/reversiones con movimientos compensatorios trazados.
- Dashboard: cuotas pagadas/parciales/pendientes, capital pagado, intereses
  recaudados y saldos a corte, todos conciliables con sus movimientos.

Decisiones pendientes antes de automatizar: orden de imputacion de parciales y
atrasados; excedentes; fechas retroactivas/cortes; tolerancias de cierre;
correcciones/reversiones; y cualquier cargo por mora. No inventar estas reglas.
El diseno debe mostrar ejemplos COP y las consecuencias de cada decision.

Orden de trabajo: corregir UX local y disenar 2B -> resolver reglas pendientes
-> implementar aplicaciones -> pruebas focalizadas y concurrencia PostgreSQL
-> staging con pago completo/parcial, abono y pago total -> produccion con
receptor oficial verificado y permisos individuales del representante legal.
No repetir toda la suite por rutina; ampliar solo si cambia el alcance o hay
fallos que lo justifiquen. La produccion de recaudos aun no queda certificada.

### Cierre local de experiencia de recaudos: 2026-10-08

Ejecucion propia, con datos sinteticos, sin cargar `.env`, sin credenciales del
servidor y sin llamadas a proveedores. Solo los modulos
`test_recaudos_ux` y `test_recaudos_breb`: PostgreSQL 16.13 temporal en Docker,
64 pruebas en 137.857 segundos, OK y cero omisiones. Incluye las cinco
concurrencias: mismo reporte, mismo movimiento global, escaneo unico,
observacion vacia con replay y dos reportes reutilizando evidencia con distintas
referencias. Se conservan locks del reporte/cuenta y se bloquean soportes en
orden estable al revalidar escaneo, contenido, tamano y hash. Django destruyo
la base de pruebas; se verifico ausencia de conexiones y se elimino el contenedor.

El recorrido `test_recaudos_breb_browser` termino OK: una prueba en 26.725 segundos
en Edge headless, locale es-CO, 375x667, 390x844 y escritorio 1280x900.
Incluye POST decimal realmente serializado, tabla contenida, errores conservados
sin JavaScript y confirmacion bancaria con nota vacia. No certifica Safari/iPhone
o Chrome/Android reales. Muestras sinteticas fuera del repositorio:
`C:/Users/User/AppData/Local/Temp/fundetec-breb-movil-smij5538`.

Una prueba adicional detecto un error que se ocultaba tras una confirmacion
inmutable con payload diferente. Se corrigio solo el resumen de errores; el
ingreso historico no cambia. Check sin incidencias, makemigrations sin cambios,
diff --check y node --check del test modificado correctos. No se ejecuto la
suite educativa completa ni se implementaron aplicaciones contables, saldos
reales, politicas pendientes, migraciones nuevas o cambios de la API contractual.

## Cierre del canal BRE-B: 2026-11-04

Base local main 300bf42bef117c85a1d31be6fa993736371c59d8, inicialmente limpia.
El ultimo despliegue informado por el usuario coincide con el codigo. Infraestructura
instalada y flag productiva false son antecedentes aportados, no verificados aqui.
No se consultaron servidores ni se activo el canal.

Faltantes concretos corregidos: enlace de pagos desde finanzas (destino real de
reanudacion de APPROVED); validacion de bytes/hash/tamano antes de servir QR o
soportes y antes de activar receptor; receptor inutilizable si falta o cambia
su QR; POST de version sustituida exige recarga sin mostrar el QR nuevo; POST
vacio conserva errores. Se muestran banco y cuenta enmascarada. Replay, permisos,
propiedad, SAFE, referencia unica global e inmutabilidad conservados. Una respuesta
privada entrega los mismos bytes verificados, no reabre un archivo modificable.

Procedimiento y datos operativos: `docs/ACTIVACION_CANAL_BREB.md`, con diagnostico
sin secretos, permisos exactos, staging conocido, produccion parametrizada con
identidades verificadas y rollback sin borrar evidencia. La plantilla productiva
parametrizada se agrega en el cierre UX posterior; exige contrastar la unidad
instalada, sin inventar nombre ni rutas.

### Cierre UX movil posterior (2026-11-04, pendiente de versionar)

Se conserva el despliegue confirmado por Carlos como antecedente, no como prueba
del codigo local sin commit. Pagos separa cuota/capital contractuales de saldos,
presenta llave, titular, cuenta enmascarada y QR privado versionado; instrucciones
secundarias en details. Reporte corto, errores con datos conservados y seguimiento
visual hasta ingreso confirmado pendiente de aplicacion. Funciona sin JavaScript.
Conciliacion agrupa evidencia propia SAFE y confirmacion bancaria, conserva la
justificacion obligatoria de diferencias. No altera contratos ni aplica dinero.

Plantilla productiva: `deploy/systemd/fundetec-production-receipts-scan.service.template`,
no es copia instalable de staging: identidades/rutas requieren contraste, condicion
production + canal habilitado, escritura limitada al almacenamiento privado y
SIGTERM. Instalacion y rollback en `docs/ACTIVACION_CANAL_BREB.md`.
No se instala nada en servidores. Validacion actual: 79 pruebas seleccionadas
(test_recaudos_ux, test_recaudos_breb, test_recaudos_production_unit), 108.852 s:
74 aprobadas y cinco concurrencias PostgreSQL SKIPPED. Nueva comprobacion Docker:
pipe dockerDesktopLinuxEngine ausente; no se creo contenedor ni base compartida.
Browser final: una prueba OK en 12.993 s, Edge 375x667, 390x844, 1280x900,
copia de llave, QR, formulario, seguimiento, conciliacion y errores sin JS;
ingreso confirmado consultado por estudiante sin marcar aplicacion completada.
Capturas sinteticas fuera del repositorio:
`C:/Users/User/AppData/Local/Temp/fundetec-breb-movil-52u6xf77`.
Inspeccion visual movil/escritorio sin desbordamiento; no sustituye certificacion
fisica Safari/iPhone ni Chrome/Android con banco real. Check sin incidencias,
makemigrations sin cambios; node --check de ambos scripts y Bash -n de once
bloques operativos correctos. systemd-analyze no disponible en Windows: verificar
la unidad renderizada en Linux antes de instalar. Pruebas automaticas de plantilla
verifican comando continuo, identidades, rutas, protecciones y condicion cerrada.
Las cifras de la seccion anterior son evidencia historica, no repeticion actual.

Validacion propia: seleccion inicial de 90 pruebas (test_recaudos_ux,
test_recaudos_breb y test_dashboard_obligaciones), 171.894 segundos: 85 OK y
5 concurrencias PostgreSQL SKIPPED. Docker Desktop no tiene motor disponible;
no se instalo ni se inicio, ni se consultaron bases compartidas. No presentar
la validacion PostgreSQL de octubre como una ejecucion actual.
Tras el ajuste final: tres pruebas OK en 14.382 segundos, incluida no reapertura
del soporte, version sustituida y browser con enlace finanzas -> pagos.
Browser: Edge headless, 375x667, 390x844, 1280x900, es-CO y confirmacion sin JS;
capturas sinteticas en `C:/Users/User/AppData/Local/Temp/fundetec-breb-movil-d_0ejvh9`.
Un intento previo no arranco por una instalacion temporal incompleta de Playwright;
se uso despues la cache local ya disponible, sin instalar dependencias.
Check: cero incidencias. Makemigrations --check --dry-run: sin cambios.
Diff --check, node --check del test modificado y sintaxis Bash de los siete
bloques del runbook: OK. No se ejecuto la suite educativa completa.

Pendientes antes de activar dinero real: receptor/QR/llave oficiales cotejados,
permisos del conciliador y verificacion bancaria, salud y ambiente de web/scanner,
certificacion staging/iPhone/Android y concurrencias en PostgreSQL aislado.
Orden siguiente: confirmar politica 2B -> libro atomico e idempotente ->
dashboard contable desde aplicaciones -> distribucion versionada con convenio
confirmado -> notificaciones de cada evento por outbox -> envio privado/idempotente
del plan PDF con version y momento aprobados. Una notificacion de reporte puede
definirse antes del libro, pero nunca anunciar aplicacion ni cuota pagada.
No hay commit, push, despliegue, cambios de servidor o llamadas a proveedores reales.

### Ajustes finales BRE-B (2026-11-05)

QR visible desde el inicio, enlace Ya transferi hacia formulario enfocable con y
sin JavaScript. Estado del estudiante: Transferencia confirmada, con una unica
explicacion de aplicacion pendiente. Verificacion de seguridad completada no
equivale a validacion bancaria. Justificacion desplegable: abre al cambiar monto,
conserva datos/errores y el servidor sigue exigiendola sin JavaScript.
Runbook distingue primera instalacion y actualizacion del scanner, con reversion
para cada caso y sin duplicar procesos. Las cinco concurrencias siguen pendientes;
comando y condiciones de PostgreSQL aislado de pruebas en ACTIVACION_CANAL_BREB.md.

Regresiones de este cierre: 36 pruebas UX y dos de plantilla aprobadas. El browser
inicial fallo por no admitir el fragmento del enlace conservado en un redirect;
se ajusto solo esa expectativa y se repitio el browser: OK, 11.896 s.
Son 39 pruebas distintas cubiertas; no se repitio la suite completa ni PostgreSQL.
Capturas finales sinteticas fuera del repositorio:
`C:/Users/User/AppData/Local/Temp/fundetec-breb-movil-22a_66nj`.
Sintaxis de ambos scripts, doce bloques Bash y diff --check correctos.
Sin commit, push, despliegue, cambios de servidor ni llamadas reales.
