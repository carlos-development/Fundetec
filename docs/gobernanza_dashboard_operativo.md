# Gobernanza del dashboard operativo

## Objetivo y alcance

El dashboard operativo permite al personal interno autorizado de Aprobado
consultar globalmente la financiacion educativa. Es independiente del dashboard
institucional y de Django Admin. La consulta contractual de esta fase es de solo
lectura: no registra pagos, concilia ni reintenta procesos. La revision
documental ya implementada es una capacidad independiente: permite aceptar
documentos o solicitar su correccion con permisos especificos; no equivale a
aprobar la solicitud ni a autorizar el curso sin firma valida.

## Principio de minimo privilegio

### Consulta contractual educativa

La seccion Obligaciones reutiliza `acceder_dashboard_operativo` y
`consultar_solicitudes_operativas`. Consultar no concede permisos de decision
ni de pagos. Los operadores internos autorizados tienen alcance global; una
membresia institucional no concede acceso a esta seccion. El filtro Institucion
se aplica al listado y a sus indicadores, no a una jerarquia academica inferida.

Solo cuentan contratos con autorizacion efectiva, firma y fotografia coherentes.
Los snapshots en tramite se muestran por separado. El calendario y sus saldos
son programados, no una contabilidad de pagos. Ver criterios y verificaciones
pendientes en [roadmap educativo](ROADMAP_FINANCIACION_EDUCATIVA.md).

Cada operador debe recibir solo los permisos necesarios para su funcion. Ser
`staff`, pertenecer a una institucion o conocer una URL no concede acceso. Los
superusuarios conservan el comportamiento normal de permisos de Django y deben
reservarse para administracion tecnica excepcional.

Permisos de consulta disponibles:

| Permiso | Capacidad |
| --- | --- |
| `acceder_dashboard_operativo` | Entrar al modulo operativo |
| `consultar_solicitudes_operativas` | Consultar solicitudes e instituciones |
| `consultar_documentos_validaciones_operativas` | Consultar estados documentales e IA controlada |
| `consultar_procesos_excepciones_operativas` | Consultar automatizacion, firma y outbox controlado |
| `consultar_datos_integrales_operativos` | Ver datos personales completos en el detalle |

## Roles previstos

- **Administrador de financiacion educativa:** consulta integral para coordinar
  la operacion. Cada capacidad de decision requiere permisos independientes.
- **Revisor documental:** consulta solicitudes, documentos y validaciones. Para
  aceptar documentos o solicitar correcciones necesita los permisos explicitos
  de revision y decision documental. El acceso a datos integrales debe
  justificarse por sus funciones.
- **Operador de consulta o auditor:** consulta solicitudes y trazabilidad con
  datos personales limitados.

Los roles se implementan temporalmente mediante grupos y permisos de Django. No
deben asignarse permisos de decision para habilitar la consulta contractual.

La revision documental requiere `acceder_revision_documental_operativa` y
`consultar_documentos_validaciones_operativas`, ademas del acceso operativo.
Las decisiones requieren tambien `decidir_revision_documental_operativa`.
Sus acciones usan POST, proteccion CSRF y los servicios de dominio existentes;
la consulta de obligaciones no concede ninguna de estas capacidades.

## Recaudos BRE-B (Fase 2A)

`consultar_recaudos_educativos` permite consultar la bandeja y soportes seguros,
ademas de `acceder_dashboard_operativo`. `conciliar_recaudos_educativos` es
independiente: solo debe asignarse al representante legal identificado por
Carlos, en su cuenta individual activa. No se asigna automaticamente por una
migracion, por ser staff ni por membresia institucional.

`configurar_receptor_breb` permite versionar y activar el receptor en Admin;
`escanear_comprobantes_educativos` permite recuperacion manual auditada del
antivirus. Las evidencias, reportes y conciliaciones son de solo lectura en
Admin. No hay reversion, borrado ni edicion de un ingreso confirmado en 2A.
La evidencia del banco es distinta del comprobante declarado del pagador.
Confirmar un ingreso no aplica dinero a cuotas/capital ni modifica la API,
autorizacion o contrato. No se agregan notificaciones ni copias nuevas.

Activacion y recuperacion: [recaudos BRE-B](RECAUDOS_BREB_FASE_2A.md).

## Ciclo de acceso

1. El responsable del area solicita el alta y justifica rol y permisos.
2. Un administrador autorizado de Aprobado valida identidad, vinculacion y
   necesidad operativa.
3. El usuario se crea o valida y se asigna al grupo de minimo privilegio desde
   Django Admin.
4. Los permisos efectivos se revisan antes de notificar la activacion.
5. Los cambios de rol siguen el mismo control y retiran permisos innecesarios.
6. La salida laboral, cambio de funcion o incidente exige revocacion inmediata.

Los accesos deben revisarse periodicamente. Las cuentas son individuales y esta
prohibido compartir usuarios, sesiones o contrasenas. El personal institucional
no recibe permisos operativos internos por su membresia institucional.

## Trazabilidad y responsabilidad

Django Admin es la herramienta temporal para administrar grupos y permisos. El
operador que realiza altas, cambios o revocaciones debe quedar identificado en
el procedimiento interno. Las decisiones documentales existentes registran
actor, accion, observacion o motivo, fecha y documento afectado mediante los
servicios de revision. La conciliacion BRE-B y la recuperacion de sus escaneos
registran actor, fecha, evidencia y motivo con permisos independientes.

Django Admin permanece disponible como respaldo tecnico; no debe usarse para
eludir los servicios de dominio. La gestion autoservicio de operadores y la
auditoria avanzada quedan fuera de esta fase.
