from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'
    verbose_name = 'Conciergerie API'

    def ready(self):
        from . import signals  # noqa: F401 — enregistre les récepteurs post_save/post_delete
