# Financiacion educativa

Dominio aislado para solicitudes educativas originadas por instituciones. No
depende de servicios de `gestion_creditos`.

## Orquestacion de solicitud e invitacion

La creacion institucional entra por
`POST /api/v1/financiacion-educativa/solicitudes/` y se ejecuta mediante
`crear_solicitud_institucional_orquestada`.

1. La solicitud y su registro de idempotencia se crean en una transaccion.
2. Solo una solicitud nueva genera una invitacion inicial y una
   `EntregaInvitacionContinuacion` pendiente.
3. Un callback registrado con `transaction.on_commit` entrega el enlace por
   correo mediante el puerto propio del dominio.
4. El callback captura los fallos del backend y marca la entrega como fallida
   sin convertir la respuesta HTTP 202 en un error.
5. Un replay, incluso con otra clave idempotente y la misma referencia
   compatible, devuelve la solicitud existente sin crear ni reenviar una
   invitacion.

`transaction.on_commit` se ejecuta en el proceso de la peticion y no constituye
procesamiento asincronico. El timeout del backend limita el tiempo de espera.

## Recuperacion y reemision

Ejecutar el comando:

```powershell
venv\Scripts\python.exe manage.py procesar_entregas_invitacion --limit 50
```

El comando selecciona entregas fallidas o estancadas. Como el token y la URL no
se persisten, la recuperacion crea una invitacion nueva, revoca la anterior y
marca su entrega como reemplazada dentro de la misma transaccion. Nunca intenta
reconstruir el enlace previo.

La reemision manual requiere un usuario administrativo. Tanto la reemision
manual como la automatica respetan el cooldown y el limite por ventana. En todo
momento puede existir como maximo una invitacion activa por solicitud.

Las solicitudes creadas antes de esta orquestacion no se procesan
automaticamente. Pueden atenderse solo mediante una accion administrativa
explicita.

## Configuracion

| Variable | Predeterminado | Uso |
| --- | ---: | --- |
| `FINANCIACION_EDUCATIVA_INVITACION_TTL_HOURS` | `72` | Vigencia del enlace |
| `FINANCIACION_EDUCATIVA_INVITATION_REISSUE_LIMIT` | `5` | Reemisiones por ventana |
| `FINANCIACION_EDUCATIVA_INVITATION_REISSUE_WINDOW_HOURS` | `24` | Ventana del limite |
| `FINANCIACION_EDUCATIVA_INVITATION_REISSUE_COOLDOWN_SECONDS` | `300` | Espera entre emisiones |
| `FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_TIMEOUT_SECONDS` | `10` | Timeout del correo |
| `FINANCIACION_EDUCATIVA_INVITATION_RECOVERY_STALE_SECONDS` | `300` | Umbral de entrega estancada |
| `FINANCIACION_EDUCATIVA_INVITATION_RECIPIENT_HMAC_KEY` | `SECRET_KEY` | Clave del HMAC de destinatario |
| `FINANCIACION_EDUCATIVA_INVITATION_DELIVERY_BACKEND` | Backend de correo del dominio | Puerto de entrega |

El backend del dominio usa el backend de correo configurado globalmente en
Django. Puede utilizar `SafeRoutingEmailBackend` por configuracion, pero no
importa servicios del dominio historico.

## Seguridad

- La API institucional nunca devuelve el enlace ni el token.
- Solo se almacena el hash del token en la invitacion.
- La entrega almacena un HMAC con clave del destinatario, no el correo.
- Eventos, errores controlados y administracion no contienen token, URL ni
  contenido del mensaje.
- Los enlaces vencen, se consumen una vez y las respuestas invalidas no
  permiten enumerar solicitudes.
- La asociacion se realiza con el usuario autenticado que posee la sesion
  iniciada desde el enlace. En esta fase no se exige coincidencia entre el
  correo de la cuenta y el de la solicitud.

## Persistencia y concurrencia

La migracion `0006` crea el outbox y sus restricciones:

- secuencia unica por solicitud;
- una sola entrega de origen `INITIAL` por solicitud;
- una sola entrega pendiente o en envio por solicitud.

SQLite valida las restricciones y el flujo funcional. La prueba de carrera con
`select_for_update` se omite localmente y debe ejecutarse en staging con
PostgreSQL, donde existen bloqueos de fila reales.
