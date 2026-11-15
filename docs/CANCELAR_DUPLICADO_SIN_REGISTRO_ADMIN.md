# Cancelar un duplicado sin registro desde Django Admin

## Alcance del candidato (2026-11-14)

Base: `origin/main` en `846e147aa2c5290fe8e3c4af9500189ec8f3c9ba`
(merge del PR #13 del monitor). Este cambio no instala temporizadores, no
activa correos ni altera flags. No requiere migraciones ni variables nuevas.
La anulacion amplia previamente preparada permanece en otro worktree; no forma
parte de esta entrega. Tampoco permite cancelar contratos enviados o firmados.
Los expedientes productivos ya resueltos no se vuelven a consultar ni operar.

## Controles

- Solo un superusuario activo y staff; se revalida su cuenta en la base.
- Solo una solicitud por URL/operacion. No existe accion masiva ni busqueda por
  correo, cedula o nombre para decidir que otras solicitudes modificar.
- Estado exacto `PENDING_USER_REGISTRATION`, sin `usuario`, consumo de invitacion
  ni historial de etapas posteriores ni fecha de matricula efectiva. Datos
  iniciales de la API no son avance.
- Cualquier relacion documental, participante, consentimiento, matricula,
  automatizacion, contrato, firma, recaudo o nueva relacion con datos bloquea.
  Una simulacion Sandbox activa tambien bloquea.
- `SENDING` o `AMBIGUOUS` del outbox, incluso con lease vencido, bloquean.
  Una entrega de invitacion `SENDING` tambien bloquea. No se fuerza el resultado
  de un SMTP incierto ni se convierte un envio en curso en cancelado.
- Se bloquean solicitud, invitaciones, entregas y outbox de ese UUID. PostgreSQL
  `NOWAIT` rechaza contencion, sin esperar en orden inverso a asociacion/outbox.
  El usuario debe recargar y revisar; ningun cambio parcial se conserva.
- Referencia externa exacta (mayusculas y espacios), motivo de 12 a 1000
  caracteres y confirmacion explicita. No incluir PII ni secretos en el motivo.
- Se usa `transicionar_solicitud` para `CANCELLED`, con actor, motivo y metadata
  `DUPLICATE_REGISTRATION_CANCELLED`. Repetir misma solicitud/actor/motivo es
  idempotente. No se elimina historial ni se libera la referencia idempotente.
- Invitaciones `ACTIVE`, incluso vencidas, pasan a `REVOKED` con su evento.
  Entregas `PENDING` pasan a `CANCELLED`; outbox `PENDING`/`RETRYING` a
  `SUPERSEDED`. `SENT`/`FAILED` y sus evidencias se conservan. No se encola correo
  de cancelacion. Copias tardias o reintentos forzados tampoco se envian si la
  solicitud esta cancelada (`APPLICATION_CANCELLED`).
- API: estado publico existente `CANCELLED`, curso no autorizado y
  `financial_terms=null`. No cambia el contrato de la API ni el replay de POST.

## Uso en Admin (despues de aprobar y liberar)

1. Entrar como superadministrador. Abrir el detalle individual de la solicitud
   por su referencia, corroborar institucion y UUID. No operar expedientes ya
   resueltos ni seleccionar la otra financiacion de la misma persona.
2. Abrir **Cancelar duplicado sin registro**. Si hay un bloqueo, no editar estados,
   usuario, leases ni historial para sortearlo. Resolverlo por el procedimiento
   correspondiente y revisar de nuevo; para expedientes avanzados esta accion
   no corresponde. La verificacion se repite al confirmar.
3. Escribir la referencia exacta, un motivo verificable, marcar confirmacion y
   confirmar. Revisar `CANCELLED`, historial con actor/motivo, invitacion revocada
   y correos pendientes detenidos. La otra solicitud debe seguir intacta.

El boton puede aparecer para una solicitud pendiente sin cuenta y mostrar luego
un bloqueo por evidencia de avance. No constituye por si mismo elegibilidad.
Una entrega historica enviada permanece `SENT` aunque su invitacion se revoque.

## Certificacion de staging (no ejecutada por este cambio)

Solo despues de aprobacion, commit/publicacion y del despliegue autorizado:

```bash
cd /var/www/fundetec-staging/current
sudo -u fundetec-staging git status --short
sudo -u fundetec-staging git rev-parse HEAD
staging_manage check
staging_manage makemigrations --check --dry-run
```

No hay `migrate` nuevo. Usar el procedimiento de despliegue vigente para un SHA
aprobado; no copiar un hash provisional ni reemplazar unidades o configuracion.
Ejecutar pruebas solo con settings de TEST y PostgreSQL aislado con credenciales
locales sinteticas, nunca contra la base compartida ni usando SMTP real:

```bash
python manage.py test financiacion_educativa.tests.test_cancelacion_duplicados_admin --noinput -v2
```

En el navegador, crear datos sinteticos por los mecanismos existentes y probar:

1. Duplicado pendiente sin cuenta y otra solicitud de la misma persona en
   `PENDING_SIGNATURE`. Cancelar solo el duplicado. Verificar invitacion,
   auditoria, outbox y que la otra solicitud/contrato siguen identicos.
2. Comprobar acceso denegado con staff no superusuario, motivo/referencia erroneos,
   y avance entre abrir formulario y confirmar. Ninguno debe modificar datos.
3. Comprobar bloqueo por envio reclamado/ambiguo en pruebas aisladas; repetir la
   confirmacion valida sin duplicar historial. No iniciar SMTP ni ZapSign para
   fabricar un bloqueo y no usar expedientes productivos.

## Reversion

Retirar el punto de entrada volviendo al SHA anterior por el procedimiento de
release vigente; no hay esquema que revertir. Conservar todos los registros ya
cancelados y sus evidencias. No ejecutar UPDATE, borrar historias, reactivar
invitaciones ni volver a `PENDING_USER_REGISTRATION`: una cancelacion confirmada
no tiene reversa automatica y requiere una decision operativa independiente.
Apagar un worker no sustituye la resolucion de un envio ambiguo.

## Evidencia local

PostgreSQL 16.13 en Docker temporal, puerto exclusivo de loopback, almacenamiento
tmpfs y credenciales sinteticas. Sin acceso a staging/produccion ni proveedores.
Configuracion local: `DJANGO_LOAD_DOTENV=false`, `DEBUG=true`,
`DEPLOYMENT_ENVIRONMENT=test`, `USE_SQLITE=false`, `DATABASE_URL` del contenedor.
El modulo nuevo fija correo locmem, proveedores/automatizacion deshabilitados
salvo el ensayo explicito sin proveedor y URLs de `example.test`.

- Regresion relacionada: 158 pruebas OK, 183.368 s, cero omisiones. Incluye una
  version anterior del modulo nuevo: no sumar ambos recuentos como casos unicos.
- Cierre del modulo nuevo completo: 34 pruebas OK, 55.967 s, cero omisiones.
  Incluye cuatro pruebas PostgreSQL con conexiones independientes: cancelacion
  simultanea, asociacion sosteniendo invitacion, reclamacion de correo en curso
  y ausencia de reclamacion despues de cancelar. No se agregan sleeps.
- Caso de la misma persona: datos sinteticos de identidad/correo compartidos;
  otra solicitud PENDING_SIGNATURE, contrato SENT_FOR_SIGNATURE, fotografia,
  cuotas y correo propios conservados campo por campo.
- Replay institucional posterior a cancelar no crea otra solicitud, invitacion,
  entrega ni correo y no reabre el expediente.
- `manage.py check`: cero issues. `makemigrations --check --dry-run`: sin cambios.
  `git diff --check`: correcto. No se requiere migracion.

Comando de regresion ejecutado (con la configuracion aislada anterior):

```bash
python manage.py test \
  financiacion_educativa.tests.test_cancelacion_duplicados_admin \
  financiacion_educativa.tests.test_invitaciones \
  financiacion_educativa.tests.test_asociacion \
  financiacion_educativa.tests.test_asociacion_concurrencia \
  financiacion_educativa.tests.test_fase6_orquestacion \
  financiacion_educativa.tests.test_outbox_correos \
  financiacion_educativa.tests.test_copias_correo_educativo \
  financiacion_educativa.tests.test_recordatorios_correo \
  financiacion_educativa.tests.test_api_institucional \
  financiacion_educativa.tests.test_cancelacion_sandbox \
  --noinput --keepdb -v1
```

El cierre posterior repitio el modulo nuevo completo con `-v2`. El contenedor y
sus bases temporales se eliminan al terminar; no se conserva un dataset real.
La certificacion de navegador en staging sigue pendiente del despliegue
autorizado. No se afirma que se hayan instalado unidades o activado correos.
