from django.urls import path

from . import views


app_name = 'institucion'

urlpatterns = [
    path('', views.inicio_view, name='inicio'),
    path('seleccionar/', views.seleccionar_institucion_view, name='seleccionar'),
    path('cambiar/', views.cambiar_institucion_view, name='cambiar'),
]
