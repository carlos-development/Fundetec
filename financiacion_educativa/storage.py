import os

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.utils.module_loading import import_string


class PrivateFileSystemStorage(FileSystemStorage):
    """Filesystem storage without any public URL mapping."""

    @property
    def base_location(self):
        return settings.FINANCIACION_EDUCATIVA_PRIVATE_ROOT

    @property
    def location(self):
        return os.path.abspath(self.base_location)

    def url(self, name):
        raise ValueError('Los documentos privados no tienen una URL publica.')


def private_document_storage():
    backend_class = import_string(
        settings.FINANCIACION_EDUCATIVA_PRIVATE_STORAGE_BACKEND
    )
    return backend_class()
