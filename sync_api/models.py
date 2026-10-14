from django.db import models
from django.contrib.auth.models import User
import hashlib
import time

class SyncFile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sync_files')
    path = models.CharField(max_length=512, db_index=True)
    is_directory = models.BooleanField(default=False)
    content = models.BinaryField(null=True, blank=True)
    size = models.BigIntegerField(default=0)
    etag = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'path')
        ordering = ['path']

    def __str__(self):
        type_str = "DIR" if self.is_directory else "FILE"
        return f"[{self.user.username}] {type_str}: {self.path} ({self.size} bytes)"

    def update_content(self, data: bytes):
        self.content = data
        self.size = len(data)
        self.etag = f'"{hashlib.md5(data).hexdigest()}"'
        self.is_directory = False
        self.save()

    @classmethod
    def create_directory(cls, user, path):
        path = path.rstrip('/') + '/'
        etag = f'"{hashlib.md5(f"{path}_{time.time()}".encode()).hexdigest()}"'
        obj, _ = cls.objects.update_or_create(
            user=user,
            path=path,
            defaults={
                'is_directory': True,
                'content': None,
                'size': 0,
                'etag': etag,
            }
        )
        return obj


class GeminiQuota(models.Model):
    """Per (api-key, model) rate-limit cooldown state for the Gemini router.

    ``provider_id`` is ``"<key_index>:<model>"`` — the raw API key is never
    stored, only its position in the configured key list.
    """

    provider_id = models.CharField(max_length=160, unique=True, db_index=True)
    cooldown_until = models.FloatField(default=0.0)  # epoch seconds; 0 = available
    last_error = models.CharField(max_length=200, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.provider_id} until={self.cooldown_until}"
