# Roadmap de financiacion educativa

Actualizado: 2026-10-06. Producto independiente de libranza y gestion_creditos.
El backlog legado se conserva en `archivo/legacy/` y no gobierna este roadmap.

## Estado y criterios de cierre

| Bloque | Estado | Criterio de cierre |
| --- | --- | --- |
| Firma, webhook y API | Cierre verificado en produccion para los dos casos Jowalth | JOW-4FE29332-PROD y JOW-88C00418-PROD devuelven APPROVED, course_authorized=true, fecha de autorizacion y financial_terms. Las fechas de firma del proceso y del artefacto coinciden. Verificacion operativa confirmada por Carlos; no constituye certificacion de otros casos. |
| Dashboard financiero inicial | Implementado y validado localmente en PostgreSQL; pendiente revision y staging | Indicadores y listado reconcilian, solo cuentan contratos realmente autorizados, excluyen simulaciones, muestran el calendario firmado y funcionan en movil. |
| Registro y conciliacion de pagos | Pendiente prioritario antes del primer vencimiento | Registrar pago y evidencia, identificar obligacion, conciliar con fuente bancaria, evitar duplicados y aplicar atomicamente con actor e historial. |
| Saldos y causacion de intereses | Pendiente de pagos | Saldo real y capital/intereses aplicados derivan del libro de pagos conciliados; calculos reproducibles a fecha de corte y separados del calendario programado. |
| Mora | Pendiente de politica aprobada | Aprobar regla, tasas, base y fechas; implementar y probar sobre pagos conciliados. El dashboard inicial no calcula cargos por mora. |
| Abonos y pago total | Pendiente | Pagos anticipados con intereses a la fecha, reduccion de plazo manteniendo cuota, nuevas proyecciones trazables y cierre sin alterar el contrato original. |
| Experiencia movil y revision humana | Implementada; verificacion de dispositivos pendiente | Probar Safari/iPhone y Chrome/Android, captura frente/reverso y recuperacion, permisos y revision humana con trazabilidad. |
| E2E menor con tutor | Cubierto localmente; certificacion staging pendiente | Tutor como unico firmante, evidencias y consentimientos correctos, firma/webhook/API completos e idempotentes. |
| Accesos individuales y notificaciones institucionales | Implementados; comprobacion operativa pendiente | Membresias explicitas y activas, aislamiento, cuentas individuales y matriz de destinatarios/outbox verificada. |
| Respaldos, restauracion y alertas | Verificacion operativa pendiente | Restauracion ensayada de PostgreSQL y archivos privados; alertas de workers, outbox, errores y firmas ambiguas, con responsables. |
| Integracion y jerarquia Institucion -> Programa -> Curso | API implementada; jerarquia pendiente | Certificar integracion y replay; disenar y migrar jerarquia explicita sin inferir relaciones a partir de nombres academicos. |

## Politica financiera aprobada

COP, metodo frances, tasa mensual 1%, originacion 10%, IVA de originacion 19%,
garantia 2%, seguro de vida 0,3711%. Los cargos y el calendario de cada contrato
se consultan desde su fotografia versionada, no se recalculan en el dashboard.
Pagos anticipados: intereses a la fecha y reduccion de plazo manteniendo cuota.
No existe una politica de cargos por mora aprobada para este incremento.

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

El dashboard no acredita pagos: recaudos, capital pagado, intereses recaudados,
mora efectiva y saldo real figuran como no disponibles. Una suma vacia de
contratos elegibles si es cero; eso no significa una cartera conciliada.

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
