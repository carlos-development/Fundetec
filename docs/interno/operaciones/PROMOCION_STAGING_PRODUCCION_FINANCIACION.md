# Promocion de financiacion educativa a produccion

## Regla de separacion

Una solicitud pertenece al ambiente y a la institucion autenticada en el
momento de su creacion. No se cambia de Sandbox a produccion sustituyendo la
API Key ni actualizando la solicitud existente.

Si una solicitud real fue creada por error en Sandbox:

1. conservarla para auditoria;
2. comprobar que no exista una firma enviada o ambigua;
3. crear una solicitud nueva en produccion con una referencia externa y una
   clave de idempotencia nuevas;
4. confirmar su recepcion y cancelar explicitamente la solicitud Sandbox;
5. obtener nuevamente los consentimientos y generar documentos contractuales
   en produccion.

La cancelacion controlada, solo disponible en `staging` y `test`, se ejecuta
con el worker educativo detenido y despues de un diagnostico de solo lectura.
Se deben confirmar el UUID, la referencia y
el administrador responsable:

```bash
staging_manage cancelar_solicitud_sandbox \
  --application-id UUID \
  --actor-id ID_ADMIN \
  --confirm-reference REFERENCIA_EXACTA \
  --reason "Solicitud real creada por error con credencial Sandbox." \
  --confirm
```

El comando no borra consentimientos, documentos, correos, historiales ni
artefactos. Cierra la automatizacion, cancela procesos de firma no enviados,
invalida artefactos generados y conserva toda la trazabilidad. Si una firma
pudo salir al proveedor, la operacion se bloquea para exigir conciliacion.

## Barrera juridica

Antes de generar o enviar un pagare deben cumplirse simultaneamente:

- existen versiones obligatorias publicadas y vigentes; se comprueban todas
  las configuradas. Antes de habilitar el flujo se deben publicar los tres
  tipos aprobados: terminos, tratamiento de datos y autorizacion financiera;
- el contenido vigente no es un marcador `TEST`, `TESTING`, `TESTING QA`,
  `QA` o `PRUEBA`;
- el usuario de la solicitud acepto exactamente las versiones vigentes.

Cuando se publica una version nueva antes de la firma, el usuario debe
reaceptarla. La solicitud conserva su estado y expediente, pero cualquier
paquete contractual generado y no enviado queda cancelado; luego se crea una
version contractual nueva. Una firma ya enviada nunca se sustituye de forma
automatica.

El bloqueo se aplica tambien a local/test: los fixtures deben registrar
consentimientos sinteticos. No se desactiva para pasar las pruebas.

## Carga de las autorizaciones aportadas

El JSON `docs/interno/juridico/autorizaciones_educativas_borradores.json`
contiene los tres bocetos proporcionados, sin publicarlos. Ejecutar primero
la previsualizacion y despues la carga:

```bash
python manage.py cargar_borradores_terminos \
  --file docs/interno/juridico/autorizaciones_educativas_borradores.json
python manage.py cargar_borradores_terminos \
  --file docs/interno/juridico/autorizaciones_educativas_borradores.json \
  --confirm
```

La carga es atomica, idempotente y nunca sobrescribe una version existente
con otro contenido. No incluye correos, firmas ni aceptaciones por el usuario.

Antes de publicar desde Admin:

1. confirmar la revision y aprobacion del contenido;
2. completar `[CORREO Y DIRECCIÓN POR CONFIRMAR]` en el texto de datos;
3. revisar el parrafo editorial final sobre alineacion normativa de ese texto;
4. publicar las tres versiones con vigencia actual y comprobar sus hashes;
5. retirar la version historica `TESTING QA`, conservando sus aceptaciones;
6. realizar una nueva aceptacion expresa en las solicitudes aun no firmadas.

El texto `TESTING QA` y el marcador de contacto incompleto no se pueden
publicar mediante el servicio. Los textos ya publicados no se editan: se
crea una nueva version.

Git transporta el comando y el JSON; no transporta los registros de la base
local. La carga y publicacion deben repetirse en la base del ambiente destino.
Mantener `FINANCIACION_EDUCATIVA_SIGNATURE_SEND_PAUSED=true` en staging hasta
terminar la revision operativa; este parche no cambia esa variable.

## Lista de salida a produccion

1. Aprobar juridicamente y publicar las versiones reales de terminos,
   tratamiento de datos y autorizaciones aplicables.
2. Configurar textos, version juridica y datos del acreedor contractual.
3. Crear base, almacenamiento privado, dominio, correo, ClamAV, IA y firma del
   ambiente productivo sin reutilizar credenciales de staging.
4. Aplicar todas las migraciones y ejecutar `collectstatic` con manifiesto.
5. Crear una institucion y credenciales productivas de Jowalth; entregar el
   prefijo y el secreto por un canal seguro y registrar la IP autorizada si se
   habilita lista blanca.
6. Mantener visibles `environment` y `X-Aprobado-Environment`; comprobar que
   respondan `production` en la URL productiva.
7. Ejecutar un E2E controlado para adulto y otro para menor. El payload siempre
   identifica al estudiante; la fecha de nacimiento activa el paso privado de
   tutor legal. No se codifican datos del menor dentro de `program_name`.
8. Confirmar firma, webhook, autorizacion del curso, correo, auditoria y
   consulta institucional antes de aceptar trafico real.
9. Entregar a Jowalth una fecha de corte. Desde ese momento se rechaza el uso
   operativo de la credencial Sandbox para casos reales.

## Solicitudes Jowalth detectadas en Sandbox

`JOW-4FE29332` y `JOW-88C00418` deben conservarse hasta que Jowalth confirme
las nuevas referencias productivas. No se deben borrar ni promover en sitio.
Tras esa confirmacion pueden cancelarse con el comando anterior, una por una.
