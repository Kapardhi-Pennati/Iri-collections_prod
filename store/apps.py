from django.apps import AppConfig


class StoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "store"

    def ready(self):
        # ✅ Import signals so the post_save handler for Order
        # status changes is registered when the app is loaded.
        import store.signals  # noqa: F401
