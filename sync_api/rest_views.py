import json
import os
from django.http import JsonResponse, HttpResponse, FileResponse, Http404
from django.conf import settings
from .models import SyncFile

def server_status(request):
    total_files = SyncFile.objects.filter(is_directory=False).count()
    total_dirs = SyncFile.objects.filter(is_directory=True).count()
    users_count = SyncFile.objects.values('user').distinct().count()

    return JsonResponse({
        'status': 'healthy',
        'service': 'Super Productivity Django Backend & Sync Server',
        'version': '1.0.0',
        'supported_protocols': ['WebDAV (RFC 4918)', 'REST API'],
        'endpoints': {
            'webdav_sync': '/webdav/',
            'status': '/api/status/',
            'tasks': '/api/tasks/',
            'admin': '/admin/',
        },
        'statistics': {
            'synced_users': users_count,
            'synced_files': total_files,
            'synced_directories': total_dirs,
        }
    })

def list_synced_tasks(request):
    """
    Parses and returns active tasks from the most recently synced Super Productivity dataset.
    """
    sync_file = SyncFile.objects.filter(
        path__icontains='super-productivity-sync',
        is_directory=False
    ).order_by('-updated_at').first()

    if not sync_file or not sync_file.content:
        # Check any json sync file
        sync_file = SyncFile.objects.filter(
            path__endswith='.json',
            is_directory=False
        ).order_by('-updated_at').first()

    if not sync_file or not sync_file.content:
        return JsonResponse({
            'message': 'No sync files uploaded yet. Configure WebDAV sync in Super Productivity to start syncing.',
            'tasks': []
        })

    try:
        content_str = bytes(sync_file.content).decode('utf-8', errors='ignore')
        data = json.loads(content_str)
        # Super Productivity format contains: task: { ids: [...], entities: { ... } }
        task_data = data.get('task', {})
        entities = task_data.get('entities', {})
        tasks = []
        for task_id, t in entities.items():
            if t and isinstance(t, dict):
                tasks.append({
                    'id': t.get('id'),
                    'title': t.get('title'),
                    'isDone': t.get('isDone', False),
                    'timeSpent': t.get('timeSpent', 0),
                    'timeEstimate': t.get('timeEstimate', 0),
                    'created': t.get('created'),
                    'tagIds': t.get('tagIds', []),
                    'projectId': t.get('projectId'),
                })
        return JsonResponse({
            'sync_path': sync_file.path,
            'last_sync': sync_file.updated_at.isoformat(),
            'total_tasks': len(tasks),
            'tasks': tasks
        })
    except Exception as e:
        return JsonResponse({
            'error': f'Failed to parse sync data: {str(e)}',
            'raw_file_size': sync_file.size
        }, status=500)

def serve_frontend(request, path=''):
    """
    Serves the pre-built Angular frontend from dist/browser.
    Robust against SPA relative path requests.
    """
    dist_dir = os.path.join(settings.BASE_DIR, '..', 'dist', 'browser')
    if not os.path.exists(dist_dir):
        return JsonResponse({
            'message': 'Angular frontend has not been compiled yet.',
            'hint': 'Run `npm run buildFrontend:prodWeb` to compile the frontend.'
        })

    # Direct match in dist/browser
    target_file = os.path.join(dist_dir, path) if path else os.path.join(dist_dir, 'index.html')
    if os.path.exists(target_file) and os.path.isfile(target_file):
        return FileResponse(open(target_file, 'rb'))

    # If the request was for a nested asset like /admin/... or sub-routes, check relative to dist_dir
    base_name = os.path.basename(path)
    asset_extensions = ('.js', '.css', '.png', '.svg', '.woff', '.woff2', '.ico', '.json', '.webmanifest')
    if any(base_name.endswith(ext) for ext in asset_extensions):
        fallback_file = os.path.join(dist_dir, base_name)
        if os.path.exists(fallback_file) and os.path.isfile(fallback_file):
            return FileResponse(open(fallback_file, 'rb'))
        assets_file = os.path.join(dist_dir, 'assets', base_name)
        if os.path.exists(assets_file) and os.path.isfile(assets_file):
            return FileResponse(open(assets_file, 'rb'))
        media_file = os.path.join(dist_dir, 'media', base_name)
        if os.path.exists(media_file) and os.path.isfile(media_file):
            return FileResponse(open(media_file, 'rb'))

    # SPA routing fallback for regular pages
    index_file = os.path.join(dist_dir, 'index.html')
    if os.path.exists(index_file):
        return FileResponse(open(index_file, 'rb'))

    raise Http404("Frontend index.html not found.")
