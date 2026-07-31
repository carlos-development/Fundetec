from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'localhost', 'testserver'])
class LocalLoginCsrfTests(TestCase):
    host = '127.0.0.1:8001'

    def setUp(self):
        self.email = 'login-csrf@example.test'
        self.password = 'Clave-CSRF-Local-2026!'
        self.user = get_user_model().objects.create_user(
            username=self.email,
            email=self.email,
            password=self.password,
        )
        self.client = Client(enforce_csrf_checks=True)

    def test_login_uses_same_origin_referrer_policy(self):
        response = self.client.get(
            reverse('account_login'),
            HTTP_HOST=self.host,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Referrer-Policy'], 'same-origin')
        self.assertContains(
            response,
            '<meta name="referrer" content="same-origin">',
            html=True,
        )
        self.assertNotContains(
            response,
            '<meta name="referrer" content="no-referrer">',
            html=True,
        )
        self.assertIn('csrftoken', response.cookies)

    def test_login_post_accepts_valid_same_origin_csrf(self):
        login_url = reverse('account_login')
        for host in ('127.0.0.1:8001', 'localhost:8001'):
            with self.subTest(host=host):
                client = Client(enforce_csrf_checks=True)
                get_response = client.get(login_url, HTTP_HOST=host)
                csrf_token = get_response.cookies['csrftoken'].value

                response = client.post(
                    login_url,
                    {
                        'login': self.email,
                        'password': self.password,
                        'csrfmiddlewaretoken': csrf_token,
                    },
                    HTTP_HOST=host,
                    HTTP_ORIGIN=f'http://{host}',
                )

                self.assertEqual(response.status_code, 302)
                self.assertEqual(
                    str(client.session['_auth_user_id']),
                    str(self.user.pk),
                )

    def test_login_post_still_rejects_null_origin(self):
        login_url = reverse('account_login')
        get_response = self.client.get(login_url, HTTP_HOST=self.host)
        csrf_token = get_response.cookies['csrftoken'].value

        response = self.client.post(
            login_url,
            {
                'login': self.email,
                'password': self.password,
                'csrfmiddlewaretoken': csrf_token,
            },
            HTTP_HOST=self.host,
            HTTP_ORIGIN='null',
        )

        self.assertEqual(response.status_code, 403)
