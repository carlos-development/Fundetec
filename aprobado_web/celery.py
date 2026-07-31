import os

from celery import Celery


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'aprobado_web.settings')

app = Celery('aprobado_web')
app.config_from_object('django.conf:settings', namespace='CELERY')
app.autodiscover_tasks()

# No hay tareas periodicas activas en el producto educativo.
app.conf.beat_schedule = {}


@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
