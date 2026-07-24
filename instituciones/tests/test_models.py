from django.core.exceptions import ValidationError
from django.test import TestCase

from instituciones.models import CredencialAPIInstitucion, Institucion


class InstitucionModelsTests(TestCase):
    def test_crea_institucion_valida(self):
        institucion = Institucion(
            nombre_comercial='Instituto Central',
            razon_social='Instituto Central SAS',
            tipo_identificacion_tributaria=Institucion.TipoIdentificacionTributaria.NIT,
            numero_identificacion_tributaria='900123456-7',
        )
        institucion.full_clean()
        institucion.save()

        self.assertIsNotNone(institucion.id)
        self.assertTrue(institucion.activa)

    def test_credencial_no_almacena_secreto_en_texto_plano(self):
        institucion = Institucion.objects.create(
            nombre_comercial='Instituto Central',
            razon_social='Instituto Central SAS',
            numero_identificacion_tributaria='900123456-7',
        )
        credencial = CredencialAPIInstitucion(
            institucion=institucion,
            nombre='Produccion',
            prefijo_clave='inst_prod',
        )
        credencial.establecer_secreto('secreto-muy-confidencial')
        credencial.full_clean()
        credencial.save()

        self.assertNotIn('secreto-muy-confidencial', credencial.secreto_hash)
        self.assertTrue(credencial.verificar_secreto('secreto-muy-confidencial'))
        self.assertFalse(credencial.verificar_secreto('incorrecto'))

    def test_rechaza_secreto_sin_hash(self):
        institucion = Institucion.objects.create(
            nombre_comercial='Instituto Central',
            razon_social='Instituto Central SAS',
            numero_identificacion_tributaria='900123456-7',
        )
        credencial = CredencialAPIInstitucion(
            institucion=institucion,
            nombre='Insegura',
            prefijo_clave='insegura',
            secreto_hash='texto-plano',
        )

        with self.assertRaises(ValidationError):
            credencial.full_clean()
