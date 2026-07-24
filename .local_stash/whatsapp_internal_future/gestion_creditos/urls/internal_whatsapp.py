from django.urls import path

from gestion_creditos.views import internal_whatsapp as views


app_name = 'internal_whatsapp'

urlpatterns = [
    path('products/', views.ProductsView.as_view(), name='products'),
    path('simulations/', views.SimulationsView.as_view(), name='simulations'),
    path('applications/', views.ApplicationsView.as_view(), name='applications'),
    path('payroll-loan/applications/', views.PayrollApplicationsView.as_view(), name='payroll_applications'),
    path('applications/status/', views.ApplicationStatusView.as_view(), name='application_status'),
    path('credits/status/', views.CreditStatusView.as_view(), name='credit_status'),
    path('documents/', views.DocumentsView.as_view(), name='documents'),
    path('identity/validate/', views.IdentityValidateView.as_view(), name='identity_validate'),
    path('consents/', views.ConsentsView.as_view(), name='consents'),
]
