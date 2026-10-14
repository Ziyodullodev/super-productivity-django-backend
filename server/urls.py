from django.contrib import admin
from django.urls import path, re_path
from sync_api import webdav_views, rest_views, telegram_views

urlpatterns = [
    # Django Admin
    path('admin/', admin.site.urls),

    # REST API endpoints
    path('api/status/', rest_views.server_status, name='server_status'),
    path('api/tasks/', rest_views.list_synced_tasks, name='list_synced_tasks'),

    # Telegram bot webhook (secret in the path; also validated via header)
    path('telegram/webhook/<str:secret>/', telegram_views.webhook, name='telegram_webhook'),
    path('telegram/webhook/', telegram_views.webhook, name='telegram_webhook_nosecret'),

    # WebDAV sync endpoint for Super Productivity
    path('webdav', webdav_views.webdav_handler),
    re_path(r'^webdav/(?P<path>.*)$', webdav_views.webdav_handler, name='webdav'),

    # Serve built Angular frontend (dist/browser)
    re_path(r'^(?P<path>.*)$', rest_views.serve_frontend, name='serve_frontend'),
]
