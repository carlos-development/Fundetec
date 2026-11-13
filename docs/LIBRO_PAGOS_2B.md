# Libro de pagos 2B: candidato de revision

Base: `origin/main` en `f0f345d`, merge PR #10. Carlos confirma su despliegue
en produccion. Esta entrega es local y no certifica ningun otro despliegue.

## Limites de habilitacion

`FINANCIACION_EDUCATIVA_LIBRO_ENABLED=false` por defecto. Las cuatro vistas
previas funcionan sin escrituras. Sin una politica persistida, muestran
`SIMULACION_NO_APROBADA` y nunca permiten contabilizar, incluso con el flag true.
La aplicacion integra anterior conserva su permiso y recorrido en obligaciones
sin libro nuevo. No se aplican automaticamente ingresos ni se envian correos.

No existe una politica productiva aprobada ni se crea una en la migracion.
Las politicas sinteticas son escenarios de prueba, NO un convenio. No copiar
fixtures ni crear politicas en produccion para desbloquear un caso urgente.
La configuracion de politicas en Admin es de solo lectura: un alta requiere
un procedimiento posterior de aprobacion con evidencia del convenio, responsable
y fecha de vigencia. El flag no basta para aprobar una politica.

## Fuentes, invariantes y consulta

- `AplicacionCuotaEducativa` permanece intacta. Se incorpora como asiento virtual
  desde su OneToOne al ingreso y a la cuota. No hay copia, backfill ni RunPython.
- `MovimientoLibroEducativo`: ingreso, fotografia, politica, secuencia, tipo,
  capital, intereses, sin destino, fecha bancaria, registro, actor, motivo,
  idempotencia y estados antes/despues (incluyen proyeccion versionada).
- `LineaLibroEducativo`: concepto, cuota original cuando corresponde y periodo.
  Una proyeccion posterior no inventa cuotas del calendario firmado: sus lineas
  anticipadas no tienen FK a una cuota original, y el snapshot conserva su plan.
- `PoliticaLibroEducativo`: institucion explicita, version, reglas completas,
  referencia de aprobacion, aprobador y fecha inicial. Inmutable.
- Neto aplicado = aplicaciones integras + capital/intereses nuevos - compensaciones.
  Dinero sin destino = recibido - neto aplicado. NO sumar cabeceras completas:
  un remanente puede participar en otra distribucion; no es otro ingreso.
- Recibido = capital neto + intereses netos + sin destino. No asignar un
  remanente a intereses futuros, devolverlo ni declararlo ingreso adicional.
- Importes bancarios exactos a dos decimales; el motor contractual sigue usando
  Decimal y pesos HALF_UP. Nunca convertir una diferencia de 0.01 en cero.
- El capital neto no puede superar el capital financiado. La suma de capital
  restante de la proyeccion debe cuadrar con el libro.
- Firma, PDF contractual, calendario original, API, curso y APPROVED no cambian.
  Capital proyectado cero no significa paz y salvo, terminacion ni cierre legal.
- Las consultas son netas actuales de la cohorte por fecha bancaria, no una
  reconstruccion historica a corte de registro. La compensacion conserva ambas
  fechas y no reescribe la fecha bancaria del ingreso.

El tablero usa subconsultas independientes por ingreso, sin multiplicarlo por
lineas. Un ingreso parcialmente distribuido puede aparecer tanto en Aplicados
como en Pendientes, con los importes respectivos (no con el total repetido).
Confirmados = Aplicados + Pendientes. Los listados respaldan esos importes.
Seguimiento considera cobertura de ambos componentes de la cuota. Un abono
anticipado no marca arbitrariamente cuotas originales como pagadas. El detalle
advierte cuando existe proyeccion diferente del calendario contractual y enlaza
al libro desde el ingreso existente. El portal no expone actor/evidencia bancaria.
Las obligaciones con un plan posterior se separan de los indicadores del
calendario original y aparecen en Proyecciones contabilizadas posteriores,
con filtros de institucion, referencia, situacion y vencimiento del plan. El
detalle y el libro muestran ese plan guardado. No volver a cobrar cuotas del
original despues de liquidar ni presentar proyecciones como cortes historicos.
El estudiante consulta la primera cuota del plan posterior guardado, cuando
existe; el PDF sigue siendo el calendario original. El acceso a otro pago
proyectado se mantiene sujeto a revision operativa, no a una nueva liquidacion
automatica del frontend.

## Simulaciones y decisiones pendientes (COP)

Todos estos ejemplos son sinteticos. Capital de ejemplo 900000; cuota 157738,
intereses del primer periodo 9000 y capital de cuota 148738; tasa mensual 1%.

| Decision | Alternativa A | Alternativa B | Diferencia / decision requerida |
| --- | --- | --- | --- |
| Parcial 100000 | Intereses primero: 9000 I + 91000 C; capital 809000 | Capital primero: 100000 C + 0 I; capital 800000 e interes pendiente 9000 | Definir imputacion por convenio. Las dos alternativas se prueban; ninguna es default productivo. |
| Dos vencidas iguales de 157738, ingreso 160000 | Mas antigua primero: completa cuota 1 y 2262 a cuota 2 (total 11262 I, 148738 C) | Proporcional 80000 a cada cuota: 16513 I, 143487 C, usando interes 9000 y 7513 | Cambian cobertura e intereses pendientes. Solo OLDEST_FIRST esta implementado como escenario; otras alternativas bloqueadas. |
| Abono a 15 dias, ingreso 200000 | Interes 4500 + capital 195500; capital posterior 704500; conserva cuota y reduce plazo | Aplicar 200000 a capital sin cubrir intereses | B deja intereses impagos; no implementada. Requiere confirmacion de la imputacion. |
| Total a 15 dias | Capital 900000 + causado 4500 = 904500 | Cobrar tambien intereses futuros firmados | La vista previa excluye futuros; no cierra la obligacion por consultar. No cobra mora. |
| Excedente 910000 en liquidacion 904500 | 5500 sin destino, separado del dinero aplicado | Devolver, saldo a favor o futura aplicacion autorizada | No hay regla aprobada; solo identificacion sin destino. No simular una devolucion. |
| Ingreso 904499.99 para total 904500 | Bloqueo: falta 0.01 | Ajuste explicitamente autorizado de 0.01 | No hay tolerancia ni cuenta de ajuste aprobada. No habilitada. |
| Abono 200000.01, capital residual con centavos | Bloquear reproyeccion del motor en pesos | Aprobar una linea residual y ultima cuota con centavos | El motor existente redondea principal; no se pierde ese centavo silenciosamente. |
| Capital 100000 aplicado el 5 en vez del 8, registrado el 10 despues de otro pago | Bloquear nueva distribucion retroactiva | Recalculo versionado: tres dias de 100000 al 1% / 30 cambian el interes en 100 COP | Solo A implementada. El ingreso puede conservarse confirmado sin aplicar. No cambiar fecha bancaria para hacerlo pasar. |
| Parcial con interes anticipado, luego abono/total | Bloquear reproyeccion hasta resolver credito de interes | Creditar, devolver o mantener interes anticipado | No perderlo ni cobrarlo otra vez. Pendiente politica; no se elige una opcion productiva. |
| Abono con cuotas vencidas | Bloquear abono; revisar deuda vencida | Refinanciar capital vencido en nuevo plazo | No refinanciar atrasos por defecto. Pago parcial y total tienen sus escenarios propios. |
| Correccion | Compensacion completa del ultimo movimiento nuevo, sin sucesores, permiso separado y motivo | Compensar una linea o un movimiento anterior con sucesores | Solo primera alternativa sintetica implementada. Definir quien solicita/autoriza y limites. No compensa aplicaciones integras historicas. |

Reglas admitidas por el motor de escenarios: `INTEREST_FIRST` o `CAPITAL_FIRST`,
`OLDEST_FIRST`, `UNALLOCATED`, `REJECT_BEFORE_LAST`,
`EXACT_BANK_CONTRACT_HALF_UP`, `LAST_FULL_ONLY`, `SAVED_PERIODS_DAILY_30`,
abono vencido `BLOCK`, interes anticipado `BLOCK_REPROJECTION`.
No se acepta JSON incompleto ni alternativas no implementadas.

Para parciales se recorren componentes del plan guardado mas antiguo primero.
La causacion para abono/total recorre periodos guardados y fracciones DAILY_30;
conserva el causado/anticipado entre pagos, sin inventar intereses de mora ni
capitalizar intereses. Las reproyecciones usan `generar_plan_reduccion_plazo`
con tasa/cuota guardadas; nunca la configuracion financiera activa del dia.

## Transacciones, reintentos y compensaciones

Vista previa firmada de 15 minutos: ingreso, actor, tipo, version/reglas,
fecha bancaria, importe, evidencia, estado y secuencia base, distribucion y
proyeccion. No es un recibo ni un permiso de contabilizacion.

Confirmar exige acceso operativo + consultar recaudos + aplicar cuotas,
habilitacion global, politica vigente de la institucion, motivo y confirmacion.
El servicio bloquea solicitud -> ingreso -> archivos en orden de PK, sin
OUTER JOIN nullable en los locks. Revalida firma, SAFE, hash, propiedad,
finalidad de soportes y distribucion dentro de la transaccion. El cambio de
secuencia exige otra vista previa: no aplicar silenciosamente a otra cuota.

Cabecera/lineas/proyeccion se registran en una transaccion. Cada cuota completa
o parcial se deriva de componentes cubiertos; nunca se edita el calendario.
Un reintento con clave/payload iguales devuelve el mismo movimiento; payload
distinto falla. Unicidad por ingreso+clave y fotografia+secuencia en PostgreSQL.
El lock de solicitud serializa diferentes ingresos y el flujo integro anterior.
Tras empezar el libro nuevo, el flujo antiguo exige revisar la distribucion
en el libro: no puede crear una cobertura paralela sobre el calendario original.

Compensar exige adicionalmente `compensar_libro_educativo`. Servicio
`compensar(movimiento_id, actor, clave, motivo, secuencia_esperada)`; se prueba
con datos sinteticos, no se publica una accion de anulacion ni se autoriza su
uso real. Se resta exactamente el ultimo asiento completo; original y lineas
se conservan. Se bloquean sucesores y doble compensacion. El ORM impide
edicion, borrado, update y bulk; SQL directo requiere controles del administrador
de BD y queda fuera de esa proteccion. No se deshabilitan constraints.

## Migracion, staging y reversion (no ejecutados en servidores)

Migracion unica nueva: `financiacion_educativa.0034_libro_pagos`, depende de
0033, instituciones.0003 y usuario intercambiable. Crea tres tablas, FK PROTECT,
unicidades y CHECK de importes y compensacion. No modifica 0031/0032 ni filas
anteriores. Compatible con PostgreSQL; el permiso nuevo se crea en post_migrate.

1. Antes de instalar: revision del PR, copia PostgreSQL y archivos privados,
   confirmar HEAD/entorno; conservar workers/outbox sin procesamiento adicional.
   Mantener el flag de libro false y avisos BRE-B con su valor previo. No cargar
   politicas productivas. Usar solo fixtures sinteticas en BD de pruebas.
2. En staging, despues de autorizar el despliegue del SHA aprobado:

```bash
cd /var/www/fundetec-staging/current
sudo -u fundetec-staging git status --short
staging_manage migrate --plan
staging_manage sqlmigrate financiacion_educativa 0034
staging_manage migrate financiacion_educativa
staging_manage check
staging_manage collectstatic --noinput
```

El checkout/actualizacion exactos y reinicio se determinan con el SHA del PR
aprobado; no usar un pull sobre HEAD detached ni habilitar workers para probar
el libro. No ejecutar pruebas en la BD compartida: crear PostgreSQL temporal
con los mismos settings de aislamiento que la validacion local.

3. Validar ingreso sintetico ya conciliado sin duplicarlo. Abrir Libro desde
   Recaudos y las cuatro vistas previas sin politica: ningun asiento/correo debe
   aparecer. En BD temporal, probar politica sintetica + flag true, parciales,
   abono, total, centavo, excedente, compensaciones y las carreras. Comparar
   tablero/libro/listados, calendario/PDF y API antes/despues. No usar un ingreso real.
4. La activacion real queda FUERA de esta entrega: aprobar tabla de decisiones,
   alcance de compensaciones y credito de interes anticipado, crear version
   institucional con evidencia/aprobador y certificar contabilidad. Nunca usar
   una politica `SYNTHETIC` como aprobacion comercial.

Reversion: apagar flag, detener nuevas confirmaciones, conservar tablas/lineas.
Si no hay movimientos nuevos, volver al release anterior aprobado y sus static
sin revertir 0034. Si existen movimientos nuevos, NO volver a codigo que ignora
el libro (mostraria importes falsos): mantener este lector, bloquear escrituras
y conciliar. No borrar tablas ni compensar masivamente, no revertir 0031/0032,
no tocar firma ni reconstruir historico. Reversion SQL destructiva prohibida
despues de contabilizar.

## Validacion y evidencia

Las pruebas usan datos sinteticos y proveedores falsos. PostgreSQL local temporal
y capturas sin JavaScript; resultados finales se registran en el PR. No se
ejecuta la suite completa por rutina ni se consultan servidores. Revisar
`tests/test_libro_pagos.py`, `test_libro_pagos_browser.py` y las regresiones de
aplicacion integra, tablero, seguimiento, portal y PDF. Contenedor y bases
temporales se eliminan al finalizar. Capturas: `capturas/libro-pagos-2b/`.

Validacion local del 2026-11-12, PostgreSQL **16.13** temporal y aislado:

- Regresion ampliada: **177/177 OK**, 380.858 segundos, cero omisiones.
- Cierre posterior de libro, proyecciones, seguimiento, portal y navegador:
  **70/70 OK**, 160.588 segundos, cero omisiones. Son conjuntos solapados;
  no deben sumarse como casos distintos. Incluye 35 pruebas nuevas (34 Python
  de libro, cinco de ellas concurrentes, y un recorrido real de navegador).
- 24 capturas completas a 375/390/1440 px, sin JavaScript ni red externa.
- `manage.py check`: 0 issues. `makemigrations --check --dry-run`: No changes
  detected. `git diff --check` y `node --check`: codigo 0.
- Plan desde 0033: solo 0034, tres tablas nuevas y sus restricciones. SQL
  inspeccionado y migracion aplicada exclusivamente en PostgreSQL temporal.
  Ningun cambio SQL a tablas contractuales o aplicaciones integras previas.
- La primera regresion detecto que el limpiador QA desconocia las dos nuevas
  dependencias del libro. Se agregaron a su lista PRESERVADA, nunca a borrado;
  la regresion de recaudos y la prueba especifica de conservacion ya pasan.

Comando de regresion ampliada (usar BD PostgreSQL temporal, nunca compartida):

```text
python manage.py test financiacion_educativa.tests.test_libro_pagos financiacion_educativa.tests.test_libro_pagos_browser financiacion_educativa.tests.test_aplicacion_cuotas financiacion_educativa.tests.test_tablero_recaudos financiacion_educativa.tests.test_seguimiento_cartera financiacion_educativa.tests.test_portal_estudiante financiacion_educativa.tests.test_plan_contractual_pdf financiacion_educativa.tests.test_portal_operaciones_recaudos financiacion_educativa.tests.test_recaudos_breb --noinput --keepdb
```

Se uso `DJANGO_LOAD_DOTENV=false`, `DEPLOYMENT_ENVIRONMENT=test`, DEBUG local y
DATABASE_URL del contenedor temporal local. El navegador requiere
`EDU_PLAYWRIGHT_MODULE` apuntando al Playwright ya instalado y
`EDU_LEDGER_SCREENSHOTS` a una carpeta de evidencia sintetica; usa Edge headless.
No instalar un proveedor ni usar credenciales del servidor para reproducirlo.
La unica advertencia de archivos locales fue WhiteNoise por staticfiles ausente;
StaticLiveServer sirvio los assets. GLib emitio avisos UWP no bloqueantes al
probar el PDF. No hubo errores ni omisiones en las dos ejecuciones indicadas.
