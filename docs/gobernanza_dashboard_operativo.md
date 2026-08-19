# Gobernanza del dashboard operativo

## Objetivo y alcance

El dashboard operativo permite al personal interno autorizado de Aprobado
consultar globalmente la financiacion educativa. Es independiente del dashboard
institucional y de Django Admin. En esta fase no permite aprobar, rechazar,
corregir, conciliar ni reintentar procesos.

## Principio de minimo privilegio

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
  la operacion. Las acciones de dominio se habilitaran en fases posteriores con
  permisos distintos.
- **Revisor documental:** consulta solicitudes, documentos y validaciones. El
  acceso a datos integrales debe justificarse por sus funciones.
- **Operador de consulta o auditor:** consulta solicitudes y trazabilidad con
  datos personales limitados.

Los roles se implementan temporalmente mediante grupos y permisos de Django. No
deben asignarse permisos de decision existentes para habilitar este dashboard.

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
el procedimiento interno. Las futuras decisiones manuales, correcciones,
reintentos y conciliaciones requeriran permisos independientes y registro de
actor, motivo, fecha y resultado.

Django Admin permanece disponible como respaldo tecnico; no debe usarse para
eludir los servicios de dominio. La gestion autoservicio de operadores y la
auditoria avanzada quedan fuera de esta fase.
