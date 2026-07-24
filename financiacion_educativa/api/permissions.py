from rest_framework.permissions import BasePermission

from instituciones.models import CredencialAPIInstitucion, Institucion


class IsAuthenticatedInstitution(BasePermission):
    def has_permission(self, request, view):
        return (
            isinstance(request.user, Institucion)
            and isinstance(request.auth, CredencialAPIInstitucion)
        )
