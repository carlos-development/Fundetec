# Gobernanza del dashboard institucional

## Objetivo

El dashboard permite consultar solicitudes de financiacion educativa pertenecientes
a una institucion. El acceso humano se concede mediante membresias individuales y
debe respetar el principio de minimo privilegio.

## Terminologia comercial temporal

FUNDETEC es la institucion comercial principal. En el frontend institucional,
los registros actuales de `Institucion` representan ofertas financiables y se
presentan temporalmente como **Programa**. De forma independiente, el campo
`program_name` (`nombre_curso` en el dominio Django) se presenta como **Curso**
o **Curso financiado**.

Esta terminologia es exclusivamente de presentacion y no constituye una
migracion del modelo de dominio. En una fase futura se implementara una
jerarquia explicita con los niveles **Institucion**, **Programa** y **Curso u
oferta academica**.

## Roles y permisos

| Capacidad | Administrador de programa | Analista de programa | Consulta del programa |
| --- | --- | --- | --- |
| Ver inicio, indicadores y solicitudes | Si | Si | Si |
| Ver detalle y estados documentales | Si | Si | Si |
| Ver nombres completos | Si | Si | Si |
| Ver correo y telefono completos | Si | Si | No, se enmascaran |
| Ver numero documental completo | No | No | No |
| Ver condiciones financieras | Si | Si | Si |
| Aprobar, rechazar o reintentar procesos | No | No | No |
| Gestionar usuarios o credenciales API | No en esta fase | No | No |
| Exportar informacion | No | No | No |

`INSTITUTION_ADMIN` representa al responsable del programa de mayor privilegio,
pero no habilita operaciones de revision ni administracion de accesos en el
dashboard. `INSTITUTION_ANALYST` consulta el expediente para seguimiento.
`INSTITUTION_READ_ONLY` consulta los mismos estados con contacto enmascarado.

## Alta de acceso

1. La institucion solicita el acceso e identifica la persona y el rol requerido.
2. Personal autorizado de Aprobado valida identidad y vinculo institucional.
3. Un responsable autorizado aprueba el acceso bajo minimo privilegio.
4. Aprobado crea la membresia en Django Admin y registra al operador en
   `creado_por`.
5. La membresia se activa y se notifica al usuario por un canal acordado.

Las membresias se administran temporalmente desde Django Admin. Solo personal
autorizado de Aprobado puede crearlas o modificarlas. No existe gestion
autoservicio de usuarios en esta fase.

## Cambios, retiro y emergencias

- Un cambio de rol requiere una solicitud validada y debe reducir privilegios
  cuando el acceso ampliado ya no sea necesario.
- La baja normal se realiza desactivando la membresia; no debe eliminarse el
  historial.
- Un retiro laboral, perdida del vinculo o incidente de seguridad exige
  desactivacion inmediata.
- Los accesos deben revisarse periodicamente con la institucion y retirar cuentas
  sin uso o sin responsable vigente.
- Las cuentas son personales. Esta prohibido compartir usuarios, sesiones o
  contrasenas.

## Consulta de aprobacion y autorizacion (2026-11-12)

La bandeja y el detalle muestran dos hechos independientes:

- **Solicitud aprobada**: el estado actual de la solicitud es `APPROVED`.
- **Curso autorizado**: existe autorizacion contractual efectiva, consultada
  mediante `firmas_autorizadas_reales()` y su fecha de firma. Exige pagare
  vigente firmado, proceso/fotografia coherentes y evidencia de firma registrada.
  No basta `APPROVED`, una fecha aislada ni una simulacion Sandbox.

Seguimiento incluye los tramites y las solicitudes aprobadas, con o sin
autorizacion. Las rechazadas, canceladas y finalizadas continuan accesibles en
Todas las solicitudes. Los filtros avanzados y la paginacion se mantienen.
La fecha de autorizacion no se deduce del historial: una transicion historica
a `APPROVED` se describe como Solicitud aprobada, no como Curso autorizado.

Son consultas de solo lectura y sin JavaScript obligatorio. No crean ni cambian
autorizaciones, firmas, condiciones, cuotas o estados. Se conservan membresias
activas, contexto validado en servidor, aislamiento por institucion y mascaras
por rol. El detalle ajeno devuelve 404, igual que uno inexistente. No se cargan
recaudos ni se exponen evidencia bancaria, referencias/actores de Operaciones,
tokens, URLs de firma o metadatos documentales privados.

Evidencia sintetica, pruebas y revision: `PORTAL_INSTITUCION.md`.

## Reportes por radicacion

`/financiacion-educativa/institucion/reportes/` utiliza la misma membresia
activa y el mismo selector limitado a la institucion del portal. No admite
seleccionar un tenant por GET ni concede permisos operativos. Todos los roles
institucionales de consulta pueden usarlo; revocacion y enmascaramiento siguen
vigentes en los listados de respaldo.

Los cuatro indicadores comparten rango inclusivo de `creada_en` (radicacion,
zona horaria vigente de Django) y curso `nombre_curso`. Recibidas significa
todas las solicitudes radicadas del rango, no solo las pendientes de iniciar.
Aprobadas usa estado APPROVED; autorizadas usa la autorizacion contractual
efectiva existente; pendientes son APPROVED sin esa autorizacion. No se filtran
por fecha de decision, firma o autorizacion. Son estados actuales de una cohorte,
no conteos historicos de transiciones ni metricas financieras.

La grafica mensual y los totales proceden de una sola consulta agregada. Cada
cifra enlaza Solicitudes con curso/rango e indicador validados; los meses
parciales mantienen el recorte del rango original. La tabla accesible y los
enlaces funcionan sin JavaScript. No hay exportacion ni acceso a evidencia
bancaria, identificadores privados o diagnosticos de Operaciones.

Consultar un listado mas tarde vuelve a leer los estados actuales: si hay
cambios concurrentes de negocio, actualizar ambos. No se conserva una fotografia
historica ni se bloquean solicitudes al leer el reporte. Definiciones completas y
validacion en staging: `REPORTES_INSTITUCIONALES.md`.

## Separacion de credenciales

Las membresias representan personas. Las credenciales API representan
integraciones de sistemas y siguen un ciclo independiente de emision, rotacion y
revocacion. Nunca debe usarse una credencial API como sustituto de una membresia
humana ni compartirse una cuenta humana con una integracion.

## Responsabilidades

El administrador de Aprobado valida la autorizacion, aplica el cambio mediante los
servicios de membresias y conserva la trazabilidad. El responsable institucional
informa altas, cambios, retiros e incidentes oportunamente y confirma las revisiones
periodicas.

Toda creacion debe conservar `creado_por` y fechas de invitacion y activacion. Los
cambios de rol y desactivaciones usan servicios de dominio y sus timestamps. Una
auditoria historica mas detallada y la gestion autoservicio quedan para una fase
posterior.
