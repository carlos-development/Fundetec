# Recaudos educativos: propuesta de aplicaciones 2B

Base inspeccionada: main 317c2c8, 2026-10-06. Documento de diseno, no libro
implementado, migracion ejecutable ni politica nueva aprobada. Las cifras son
sinteticas. El ingreso confirmado 2A sigue pendiente de aplicacion.

## Componentes reales reutilizables

| Componente | Responsabilidad y limite actual |
| --- | --- |
| `models.CondicionesFinancieras` | Fotografia versionada, activa y bloqueada; COP, tasa, redondeo, causacion y fechas pactadas. No se modifica al cobrar. |
| `models.CuotaAmortizacionEducativa` | Calendario contractual inmutable: numero, fecha, interes, capital, valor y saldos programados. No registra pagos. |
| `models_recaudos.ConciliacionRecaudoEducativo` | Ingreso bancario inmutable, referencia unica global por cuenta, importe, fecha efectiva, evidencia y actor. No aplica al calendario. |
| `services.recaudos.confirmar_recaudo` | Serializa reporte y cuenta bancaria; replay por payload y unicidad bancaria. Mantener ambas garantias. |
| `services.motor_financiero.redondear_peso` | Decimal, peso COP, ROUND_HALF_UP; evitar floats y otro motor. |
| `services.motor_financiero.calcular_interes_causado_diario_30` | Periodo comercial 30; antes del vencimiento usa dias transcurridos acotados, al vencimiento usa 30. No resuelve varios periodos atrasados. |
| `services.motor_financiero.generar_plan_reduccion_plazo` | Proyeccion manteniendo cuota, con primer periodo parcial y ultima cuota ajustada. No reemplaza cuotas firmadas. |
| `services.proyecciones_financieras.proyectar_abono_capital` | Proyeccion hipotetica a partir de `cuotas_cubiertas`; no representa pagos parciales ni atrasos reales. |
| `services.proyecciones_financieras.proyectar_pago_total` | Liquidacion hipotetica a fecha, excluye intereses futuros; rechaza fechas posteriores al siguiente vencimiento. |
| `dashboards.operaciones.cartera.obligaciones_autorizadas` | Fotografia con firma real coherente y solicitud APPROVED; excluye simulaciones. No acredita impago. |
| `services.estado_publico` | Publica condiciones y autorizacion por firma vigente y APPROVED. PAID se mapea hoy a UNDER_REVIEW. |

No convertir `cuotas_cubiertas` ingresadas por un operador en evidencia de pago.
Reutilizar funciones matematicas puras; adaptar las proyecciones para recibir
estado derivado del libro, sin modificar silenciosamente su semantica actual.
Tasa contractual mensual 1%, metodo frances, DAILY_30 y vencimientos sin ajuste
por festivos son reglas confirmadas. No recalcular cargos con configuracion activa
distinta de la fotografia firmada.

## Modelo aditivo propuesto

Los nombres siguientes son propuestas, no clases existentes:

1. `CuentaObligacionEducativa`: OneToOne hacia la fotografia firmada,
   fecha de apertura y version del libro. Fila estable de bloqueo; sin saldo
   manual editable. Estado financiero derivado o cache reconstruible y verificable.
2. `AplicacionRecaudoEducativo`: OneToOne PROTECT a la conciliacion; FK PROTECT
   a cuenta de obligacion; importe recibido, capital aplicado, intereses aplicados,
   importe no aplicado, fecha efectiva, fecha de registro, actor, version de politica,
   numero de secuencia por obligacion y huella del payload determinista.
3. `LineaAplicacionEducativa`: FK PROTECT a aplicacion; cuota contractual opcional
   para aplicaciones a capital anticipado; concepto CAPITAL/INTEREST/UNAPPLIED,
   importe y periodo/corte de causacion para intereses. Conceptos adicionales
   requieren politica aprobada; no crear mora por defecto.
4. `EventoCompensacionAplicacionEducativa`: referencia a la aplicacion original,
   motivo, actor autorizante, fecha, version y lineas compensatorias trazables.
   No usar montos negativos en lineas ordinarias ni borrar la operacion original.
5. `ProyeccionPosteriorAplicacionEducativa`: snapshot inmutable de calendario
   proyectado ligado a la secuencia del libro, fecha efectiva y version del motor.
   Es una proyeccion auxiliar, nunca reemplaza el calendario contractual firmado.

Cuota pagada/parcial/pendiente es una proyeccion de sus componentes cubiertos;
no agregar un campo editable al calendario firmado. La cuota esta pagada solo
cuando su capital y sus intereses exigibles estan cubiertos segun politica;
una aplicacion inferior la deja parcial. Pagos a capital anticipado afectan la
proyeccion restante, no cuentan arbitrariamente como cuotas contractuales pagadas.

### Migracion prevista

Una migracion posterior a 0029, numerada al implementar sobre el HEAD real,
crearia exclusivamente tablas, FK PROTECT, indices y constraints aditivas.
Sin RunPython de aplicacion retrospectiva ni cambios de fotografias/cuotas.
Indices por obligacion+secuencia, fecha efectiva+secuencia, conciliacion y cuota.
Constraints: conciliacion unica, secuencia unica por obligacion, importes no
negativos, recibido = capital + intereses + no aplicado. Igualdad entre cabecera
y suma de lineas requiere validacion del servicio atomico; un CHECK SQL simple
no agrega filas relacionadas. Prohibir edicion/borrado y bulk bypass mediante
patron inmutable existente; SQL directo sigue siendo un limite operativo.

Ingresos previos permanecen sin aplicacion; abrir cuentas de obligacion solo
al consultar una accion de aplicacion explicita, no desde startup o un dashboard.
Rollback de codigo no elimina tablas ni movimientos; suspender nuevas aplicaciones
y conservar evidencia para conciliacion antes de habilitar un release anterior.

## Servicios y transacciones propuestos

`previsualizar_aplicacion_recaudo`: consulta autorizada, lee ingreso conciliado,
estado reconstruido y politica aprobada; devuelve distribucion y version/secuencia
base. No escribe ni garantiza que el preview siga vigente al confirmar.

`confirmar_y_aplicar_recaudo`: permiso especifico, una transaction.atomic para
confirmacion nueva y aplicacion. Orden global: solicitud (identidad estable de
la obligacion), reporte, cuenta bancaria, cuenta de obligacion; nunca invertir
este orden en compensaciones ni reintentos. Mantener los locks del servicio 2A
y unique(cuenta, referencia). Resolver cuenta de obligacion OneToOne bajo el
lock de solicitud, sin carreras de creacion. No mantener locks durante proveedores.

Releer firma/fotografia, comprobar pertenencia de todos los objetos, banco/SAFE,
referencia e importe, reconstruir estado financiero y recalcular distribucion
con la politica versionada. Un preview desactualizado requiere nueva confirmacion.
Insertar cabecera, lineas y proyeccion en la misma transaccion. Al fallar, rollback
de toda confirmacion nueva y aplicacion; no dejar un ingreso nuevo presentado
como aplicado. El resultado persistente es CONFIRMED_PENDING_APPLICATION o
APPLIED, derivado de evidencia, no de un texto de exito del navegador.

`aplicar_ingreso_confirmado`: para 2A existente o recuperacion; mismo orden de
locks y invariantes. Ante intento repetido, OneToOne a conciliacion devuelve
misma aplicacion si payload coincide; payload distinto falla sin otra escritura.
Si el commit termino pero se perdio la respuesta HTTP, replay recupera el mismo
resultado. Pagos de distintos operadores sobre una obligacion se serializan
por solicitud/cuenta de obligacion, aun con referencias bancarias diferentes.

`consultar_saldo_a_corte`: reconstruye por fecha efectiva y secuencia, separa
fecha de registro de fecha bancaria y aplica compensaciones. No escribe cortes
al consultar. Una cache, si se agrega, debe ser reconstruible desde lineas.

`compensar_aplicacion`: permiso separado, motivo obligatorio, limites y aprobacion
pendientes. Importe original y archivo permanecen intactos. Una correccion de
fecha no debe reordenar otros pagos sin reprocesamiento explicito versionado.

## Invariantes

- Solo ingresos conciliados y asociados a una obligacion real; nunca simulaciones.
- Un ingreso, una aplicacion; suma de lineas = recibido, sin dinero desaparecido.
- Capital restante = capital contractual - capital neto aplicado. No restar
  todo el ingreso ni cobrar de nuevo originacion/IVA/garantias/seguro financiados.
- Intereses recaudados se obtienen de lineas, no del interes total estimado.
- No cubrir dos veces el mismo componente ni generar capital negativo.
- Ingreso no aplicado/excedente queda identificado; no fingir pago o devolucion.
- Precision del recaudo 2A: dos decimales. Calculos financieros contractuales:
  peso HALF_UP. Aprobar el tratamiento residual sin truncar centavos del banco.
- Firma, autorizacion, `financial_terms` y APPROVED no cambian por la aplicacion.
  Cierre financiero separado en cuenta de obligacion. No reutilizar PAID: hoy
  retiraria autorizacion e indicadores; cambiar ese contrato requiere otro bloque.
- Fechas retroactivas no reescriben movimientos ya contabilizados silenciosamente.

## Ejemplos COP y decisiones que faltan

Son ejemplos de distribucion, no politicas productivas aprobadas:

| Caso | Datos | Resultado o pregunta |
| --- | --- | --- |
| Cuota completa | Capital 900000, cuota 157738 con interes 9000 y capital 148738; ingreso 157738 | Al cubrir ambos componentes: capital restante 751262, interes recaudado 9000, cuota pagada. No saldo 742262. |
| Parcial | Misma cuota, ingreso 100000 | Intereses primero: 9000 interes + 91000 capital. Capital primero: 100000 capital e interes pendiente 9000. Ambas dejan cuota parcial; escoger orden antes de automatizar. |
| Abono a 15 dias | Saldo capital 900000, 1% mensual, 15/30, ingreso 200000 | Interes a fecha 4500; si se cubre, capital 195500, saldo 704500. Reproyectar plazo conservando cuota, sin alterar original. |
| Pago total a 15 dias | Saldo 900000, interes a fecha 4500 | Liquidacion 904500; cierre solo tras ingreso aplicado completo. No cobrar intereses futuros del calendario. |
| Excedente | Liquidacion 904500, ingreso 910000 | 5500 sin destino definido. Retener identificado vs devolver: requiere politica, no cerrar sin registrar excedente. |
| Centavos | Liquidacion 904500, ingreso 904499.99 | Falta 0.01. No inventar tolerancia ni redondear el banco a 904500; definir ajuste trazado o mantener remanente. |

Preguntas concretas de negocio:

1. Parciales y atrasados: intereses primero, capital primero o proporcion? Si
   hay dos cuotas vencidas, se cubre la mas antigua antes del periodo actual?
   Ejemplo 100000 de la fila parcial produce saldos distintos. Las funciones
   actuales no causan varios periodos vencidos: definir como recorrer cortes,
   sin reutilizar una sola causacion de 30 dias para todo el atraso.
2. Excedentes: saldo a favor, devolucion o aplicacion futura autorizada? Definir
   destino de 5500, responsable, evidencia y como evita un cierre aparente.
3. Fechas retroactivas y corte: permitir registrar el 10 un pago del 5 despues
   de otro del 8? Recalculo versionado o bloqueo/rectificacion autorizada?
   Determina intereses y si debe conciliarse el libro antes de continuar.
4. Cierre/redondeo: tolerancia cero u ajuste explicitamente contabilizado?
   Quien autoriza 0.01 y en que concepto queda? No se decide en UX.
5. Compensaciones: quien solicita y quien autoriza; reversar todo o por linea;
   que hacer si ya existen pagos posteriores o devoluciones? Mantener originales.
6. Mora: existe algun cargo autorizado, base, tasa y vigencia? Sin aprobacion
   no generar monto alguno ni llamar mora a intereses contractuales ordinarios.

## Entrega futura, backfill y pruebas

Primero aprobar las seis decisiones, versionar politica y permisos; despues
migracion aditiva y libro/preview; luego accion atomica y dashboard derivado.
Ingresos 2A previos: listado acotado, preview por UUID de conciliacion y aplicacion
administrativa explicita e idempotente. No tarea que procese todo el historico
al iniciar ni cambie solicitudes. No habilitar "confirmar y aplicar" hasta existir
lineas persistentes y pruebas contables.

Pruebas futuras: cuota completa y parcial con orden aprobado; atrasados varios
periodos; abono y liquidacion a fechas frontera/comercial 30/fin de mes; centavos
y excedentes; cuota conservada y ultima ajustada; un ingreso dos operadores,
dos ingresos misma obligacion, payload alterado, respuesta HTTP perdida, rollback
despues de cada insercion y recuperacion; compensaciones/corte retroactivo;
historico acotado; dashboard conciliable; API mantiene APPROVED, autorizacion y
condiciones tras cierre financiero. Concurrencia en PostgreSQL temporal y
certificacion posterior en staging con pago completo/parcial, abono y total.
