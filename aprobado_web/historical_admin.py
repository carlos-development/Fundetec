from django.contrib import admin
from django.core.exceptions import PermissionDenied


class HistoricalReadOnlyAdminMixin:
    """Consulta historica sin acciones ni superficies de escritura."""

    actions = None
    inlines = ()

    @staticmethod
    def _is_authorized(request):
        user = getattr(request, 'user', None)
        return bool(
            user
            and user.is_active
            and user.is_staff
            and user.is_superuser
        )

    def has_module_permission(self, request):
        return self._is_authorized(request)

    def has_view_permission(self, request, obj=None):
        return self._is_authorized(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_model_perms(self, request):
        allowed = self._is_authorized(request)
        return {
            'add': False,
            'change': False,
            'delete': False,
            'view': allowed,
        }

    def get_actions(self, request):
        return {}

    def get_inline_instances(self, request, obj=None):
        return []

    def get_readonly_fields(self, request, obj=None):
        configured = tuple(super().get_readonly_fields(request, obj))
        model_fields = tuple(
            field.name
            for field in (*self.model._meta.fields, *self.model._meta.many_to_many)
        )
        return tuple(dict.fromkeys((*configured, *model_fields)))

    def get_urls(self):
        # Excluye URLs operativas agregadas por ModelAdmin historicos.
        return admin.ModelAdmin.get_urls(self)

    def save_model(self, request, obj, form, change):
        raise PermissionDenied

    def delete_model(self, request, obj):
        raise PermissionDenied

    def delete_queryset(self, request, queryset):
        raise PermissionDenied


def restrict_registered_app_admin(app_label, *, site=admin.site):
    """Re-registra los ModelAdmin de una app como consulta historica."""

    registrations = tuple(site._registry.items())
    for model, registered_admin in registrations:
        if model._meta.app_label != app_label:
            continue
        original_class = registered_admin.__class__
        if issubclass(original_class, HistoricalReadOnlyAdminMixin):
            continue

        restricted_class = type(
            f'HistoricalReadOnly{original_class.__name__}',
            (HistoricalReadOnlyAdminMixin, original_class),
            {'__module__': original_class.__module__},
        )
        site.unregister(model)
        site.register(model, restricted_class)
