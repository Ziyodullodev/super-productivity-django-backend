from django.contrib import admin
from .models import SyncFile

@admin.register(SyncFile)
class SyncFileAdmin(admin.ModelAdmin):
    list_display = ('path', 'user', 'is_directory', 'size', 'etag', 'updated_at')
    list_filter = ('is_directory', 'user', 'updated_at')
    search_fields = ('path', 'user__username')
    readonly_fields = ('size', 'etag', 'created_at', 'updated_at')
