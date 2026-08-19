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
