"""
Register (or delete) the Telegram webhook using values from settings/.env.

Usage (inside the virtualenv on the server):
    python manage.py set_telegram_webhook
    python manage.py set_telegram_webhook --delete
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from sync_api import telegram


class Command(BaseCommand):
    help = "Set or delete the Telegram bot webhook for this server."

    def add_arguments(self, parser):
        parser.add_argument("--delete", action="store_true", help="Remove the webhook instead.")

    def handle(self, *args, **options):
        token = getattr(settings, "TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise CommandError("TELEGRAM_BOT_TOKEN is not set (.env).")

        if options["delete"]:
            res = telegram.delete_webhook(token)
            self.stdout.write(self.style.SUCCESS(f"deleteWebhook -> {res}"))
            return

        base = getattr(settings, "PUBLIC_BASE_URL", "").rstrip("/")
        secret = getattr(settings, "TELEGRAM_WEBHOOK_SECRET", "")
        if not base:
            raise CommandError("PUBLIC_BASE_URL is not set (.env).")
        url = f"{base}/telegram/webhook/{secret}/" if secret else f"{base}/telegram/webhook/"
        res = telegram.set_webhook(token, url, secret_token=secret or None)
        if not res.get("ok"):
            raise CommandError(f"setWebhook failed: {res}")
        shown = url.replace(secret, "***") if secret else url
        self.stdout.write(self.style.SUCCESS(f"Webhook set -> {shown}\n{res}"))
