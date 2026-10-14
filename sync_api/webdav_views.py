import base64
import hashlib
from datetime import timezone
from email.utils import formatdate
from django.http import HttpResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from .models import SyncFile

def add_cors_and_dav_headers(response: HttpResponse) -> HttpResponse:
    response['DAV'] = '1, 2'
    response['MS-Author-Via'] = 'DAV'
    response['Access-Control-Allow-Origin'] = '*'
    response['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS, PROPFIND, MKCOL, HEAD'
    response['Access-Control-Allow-Headers'] = 'Authorization, Content-Type, Depth, If-Match, If-None-Match, X-Requested-With, Connection'
    response['Access-Control-Expose-Headers'] = 'ETag, DAV, Content-Length, Content-Type'
    return response

def get_auth_user(request: HttpRequest):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Basic '):
        return None
    try:
        decoded = base64.b64decode(auth_header[6:]).decode('utf-8')
        username, password = decoded.split(':', 1)
    except Exception:
        return None

    # Authenticate existing user
    user = authenticate(request, username=username, password=password)
    if user:
        return user

    # If user doesn't exist yet, auto-register for easy first setup
    if not User.objects.filter(username=username).exists():
        user = User.objects.create_user(username=username, password=password)
        return user

    return None

def format_http_date(dt):
    if not dt:
        return formatdate(usegmt=True)
    return formatdate(dt.replace(tzinfo=timezone.utc).timestamp(), usegmt=True)

def build_propfind_xml(files, base_prefix='/webdav'):
    xml_entries = []
    for f in files:
        href = f"{base_prefix.rstrip('/')}/{f.path.lstrip('/')}"
        if f.is_directory and not href.endswith('/'):
            href += '/'
        
        resource_type = "<D:collection/>" if f.is_directory else ""
        etag = f.etag or f'"{hashlib.md5(f.path.encode()).hexdigest()}"'
        date_str = format_http_date(f.updated_at)
        size = f.size if not f.is_directory else 0

        xml_entries.append(f"""    <D:response>
      <D:href>{href}</D:href>
      <D:propstat>
        <D:prop>
          <D:getlastmodified>{date_str}</D:getlastmodified>
          <D:getcontentlength>{size}</D:getcontentlength>
          <D:getetag>{etag}</D:getetag>
          <D:resourcetype>{resource_type}</D:resourcetype>
        </D:prop>
        <D:status>HTTP/1.1 200 OK</D:status>
      </D:propstat>
    </D:response>""")

    responses_body = "\n".join(xml_entries)
    return f"""<?xml version="1.0" encoding="utf-8" ?>
<D:multistatus xmlns:D="DAV:">
{responses_body}
</D:multistatus>"""

@csrf_exempt
def webdav_handler(request: HttpRequest, path: str = ""):
    # 1. Handle CORS preflight
    if request.method == 'OPTIONS':
        res = HttpResponse(status=200)
        res['Allow'] = 'OPTIONS, GET, HEAD, POST, PUT, DELETE, PROPFIND, MKCOL'
        return add_cors_and_dav_headers(res)

    # 2. Authentication
    user = get_auth_user(request)
    if not user:
        res = HttpResponse("Unauthorized WebDAV access", status=401)
        res['WWW-Authenticate'] = 'Basic realm="Super Productivity WebDAV"'
        return add_cors_and_dav_headers(res)

    norm_path = "/" + path.strip("/")
    if path.endswith("/") and norm_path != "/":
        norm_path += "/"

    # Ensure root exists
    root_obj, _ = SyncFile.objects.get_or_create(
        user=user,
        path="/",
        defaults={'is_directory': True, 'size': 0, 'etag': '"root"'}
    )

    # 3. Method routing
    method = request.method.upper()

    if method == 'PROPFIND':
        depth = request.headers.get('Depth', '1')
        target_path = norm_path

        # Find target resource
        target = SyncFile.objects.filter(user=user, path=target_path).first()
        if not target and target_path != '/':
            target = SyncFile.objects.filter(user=user, path=target_path.rstrip('/')).first()

        if not target and target_path != '/':
            res = HttpResponse(status=404)
            return add_cors_and_dav_headers(res)

        files = [target] if target else [root_obj]

        if depth != '0' and (target is None or target.is_directory):
            # Include children
            prefix = target_path.rstrip('/') + '/'
            children = SyncFile.objects.filter(user=user, path__startswith=prefix).exclude(path=prefix)
            if depth == '1':
                # Direct children only
                direct_children = []
                for c in children:
                    rel = c.path[len(prefix):].strip('/')
                    if '/' not in rel:
                        direct_children.append(c)
                files.extend(direct_children)
            else:
                files.extend(list(children))

        xml_response = build_propfind_xml(files)
        res = HttpResponse(xml_response, status=207, content_type='application/xml; charset=utf-8')
        return add_cors_and_dav_headers(res)

    elif method == 'GET':
        target = SyncFile.objects.filter(user=user, path=norm_path).first()
        if not target or target.is_directory:
            res = HttpResponse(status=404)
            return add_cors_and_dav_headers(res)

        body = bytes(target.content or b'')
        res = HttpResponse(body, content_type='application/octet-stream')
        res['ETag'] = target.etag
        res['Last-Modified'] = format_http_date(target.updated_at)
        return add_cors_and_dav_headers(res)

    elif method == 'PUT':
        body = request.body
        target, created = SyncFile.objects.get_or_create(
            user=user,
            path=norm_path,
            defaults={'is_directory': False}
        )
        target.update_content(body)

        status_code = 201 if created else 200
        res = HttpResponse(status=status_code)
        res['ETag'] = target.etag
        return add_cors_and_dav_headers(res)

    elif method == 'MKCOL':
        dir_path = norm_path.rstrip('/') + '/'
        if SyncFile.objects.filter(user=user, path=dir_path).exists():
            res = HttpResponse("Collection already exists", status=405)
            return add_cors_and_dav_headers(res)

        SyncFile.create_directory(user, dir_path)
        res = HttpResponse(status=201)
        return add_cors_and_dav_headers(res)

    elif method == 'DELETE':
        del_path = norm_path
        # Delete exact match
        deleted_count, _ = SyncFile.objects.filter(user=user, path=del_path).delete()
        # Delete children if directory
        prefix = del_path.rstrip('/') + '/'
        SyncFile.objects.filter(user=user, path__startswith=prefix).delete()

        res = HttpResponse(status=204)
        return add_cors_and_dav_headers(res)

    res = HttpResponse(status=405)
    return add_cors_and_dav_headers(res)
