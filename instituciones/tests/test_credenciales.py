from django.test import TestCase

from instituciones.models import CredencialAPIInstitucion, Institucion
from instituciones.services.credenciales import (
    crear_credencial_api,
    revocar_credencial_api,
    rotar_credencial_api,
)


class CredencialesAPIInstitucionTests(TestCase):
    def setUp(self):
        self.institucion = Institucion.objects.create(
            nombre_comercial='Institucion API',
            razon_social='Institucion API SAS',
            numero_identificacion_tributaria='901000001',
        )

    def test_emision_entrega_token_una_vez_y_persiste_hash(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Produccion',
            alcances=['financiacion:write', 'financiacion:read'],
        )
        prefijo, secreto = emitida.token.split('.', 1)

        self.assertEqual(prefijo, emitida.credencial.prefijo_clave)
        self.assertNotEqual(secreto, emitida.credencial.secreto_hash)
        self.assertTrue(emitida.credencial.verificar_secreto(secreto))
        self.assertNotIn(secreto, repr(emitida))

    def test_rotacion_invalida_secreto_anterior(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Produccion',
        )
        secreto_anterior = emitida.token.split('.', 1)[1]

        rotada = rotar_credencial_api(credencial=emitida.credencial)
        rotada.credencial.refresh_from_db()

        self.assertFalse(rotada.credencial.verificar_secreto(secreto_anterior))
        self.assertTrue(
            rotada.credencial.verificar_secreto(rotada.token.split('.', 1)[1])
        )

    def test_revocacion_desactiva_credencial(self):
        emitida = crear_credencial_api(
            institucion=self.institucion,
            nombre='Produccion',
        )

        revocar_credencial_api(credencial=emitida.credencial)

        self.assertFalse(
            CredencialAPIInstitucion.objects.get(pk=emitida.credencial.pk).activa
        )
