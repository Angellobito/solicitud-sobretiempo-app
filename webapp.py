import base64
from contextlib import closing
import json
import os
import re
import secrets
import sqlite3
import tempfile
import threading
import time
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from app import BOSSES, app_root, fmt_time, generate_pdf, parse_sctr, scheduled_start, user_data_dir


ROLES = ['Técnico', 'Técnico de mantenimiento', 'Ayudante técnico', 'Supervisor']
DAY_TYPES = ['Día laborable', 'Día de descanso', 'Feriado']
MAX_BODY = 12 * 1024 * 1024
HOSTED = os.environ.get('TREMOND_HOSTED') == '1'
HOSTED_SCTR = None
APPROVAL_SESSIONS = {}
APPROVAL_SESSIONS_LOCK = threading.Lock()


def records_file():
    return user_data_dir() / 'sctr-actual.pdf'


def overtime_db_path():
    directory = Path(os.environ.get('TREMOND_DATA_DIR') or user_data_dir())
    directory.mkdir(parents=True, exist_ok=True)
    return directory / 'solicitudes-sobretiempo.sqlite3'


def db_connect():
    connection = sqlite3.connect(overtime_db_path(), timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA journal_mode=WAL')
    connection.execute('''CREATE TABLE IF NOT EXISTS overtime_requests (
        request_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        request_json TEXT NOT NULL,
        management_status TEXT NOT NULL DEFAULT 'Pendiente',
        management_at TEXT,
        hr_status TEXT NOT NULL DEFAULT 'Pendiente',
        hr_at TEXT
    )''')
    connection.execute('''CREATE TABLE IF NOT EXISTS overtime_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        request_id TEXT NOT NULL,
        reviewer_role TEXT NOT NULL,
        decision TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY(request_id) REFERENCES overtime_requests(request_id)
    )''')
    return connection


def iso_now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def request_status(row):
    if row['management_status'] == 'Denegado' or row['hr_status'] == 'Denegado':
        return 'Denegado'
    if row['management_status'] == 'Aprobado' and row['hr_status'] == 'Aprobado':
        return 'Aprobado'
    return 'Pendiente'


def save_request(fields):
    request_id = secrets.token_urlsafe(9)
    created_at = iso_now()
    with closing(db_connect()) as connection, connection:
        connection.execute(
            'INSERT INTO overtime_requests(request_id, created_at, request_json) VALUES (?, ?, ?)',
            (request_id, created_at, json.dumps(fields, ensure_ascii=False)),
        )
    return request_id


def approval_session(handler):
    cookie = handler.headers.get('Cookie', '')
    token = next((part.strip().split('=', 1)[1] for part in cookie.split(';')
                  if part.strip().startswith('tremond_approval=')), '')
    with APPROVAL_SESSIONS_LOCK:
        session = APPROVAL_SESSIONS.get(token)
        if not session or session['expires'] < time.time():
            APPROVAL_SESSIONS.pop(token, None)
            return None
        return session


def list_requests():
    with closing(db_connect()) as connection, connection:
        rows = connection.execute('SELECT * FROM overtime_requests ORDER BY created_at DESC').fetchall()
        events = connection.execute(
            'SELECT request_id, reviewer_role, decision, created_at FROM overtime_events ORDER BY event_id'
        ).fetchall()
    event_map = {}
    for event in events:
        event_map.setdefault(event['request_id'], []).append(dict(event))
    result = []
    for row in rows:
        item = dict(row)
        item['fields'] = json.loads(item.pop('request_json'))
        item['status'] = request_status(row)
        item['events'] = event_map.get(row['request_id'], [])
        result.append(item)
    return result


def current_records():
    if HOSTED:
        if not HOSTED_SCTR:
            raise ValueError('Carga el SCTR vigente para habilitar la lista de técnicos.')
        return parse_pdf_bytes(HOSTED_SCTR)
    path = records_file()
    if not path.exists():
        path = app_root() / 'assets' / 'sctr-inicial.pdf'
    return parse_sctr(path)


def parse_pdf_bytes(raw):
    with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as temp:
        temp.write(raw)
        temp_path = Path(temp.name)
    try:
        return parse_sctr(temp_path)
    finally:
        temp_path.unlink(missing_ok=True)


def clean_text(value, limit):
    value = str(value or '').strip()
    if len(value) > limit:
        raise ValueError(f'El texto supera el límite de {limit} caracteres.')
    return value


class Handler(BaseHTTPRequestHandler):
    server_version = 'TremondLocalWeb/1.0'

    def log_message(self, fmt, *args):
        return

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        size = int(self.headers.get('Content-Length', '0'))
        if size <= 0 or size > MAX_BODY:
            raise ValueError('La solicitud está vacía o el archivo es demasiado grande.')
        return json.loads(self.rfile.read(size).decode('utf-8'))

    def authorized(self):
        if not HOSTED:
            return True
        access_key = os.environ.get('TREMOND_ACCESS_KEY', '')
        supplied = self.headers.get('Authorization', '')
        return bool(access_key) and secrets.compare_digest(supplied, f'Bearer {access_key}')

    def do_GET(self):
        route = urlparse(self.path).path
        if route == '/api/config':
            self.send_json(200, {
                'hosted': HOSTED,
                'approval_enabled': bool(os.environ.get('TREMOND_APPROVAL_PASSWORD')),
                'persistent_approvals': os.environ.get('TREMOND_PERSISTENT_STORAGE') == '1',
            })
            return
        if route == '/api/approval/requests':
            if not self.authorized():
                self.send_json(401, {'error': 'Clave de acceso incorrecta o faltante.'})
                return
            session = approval_session(self)
            if not session:
                self.send_json(401, {'error': 'Inicia sesión en Validación e historial.'})
                return
            try:
                self.send_json(200, {'requests': list_requests(), 'role': session['role']})
            except Exception as exc:
                self.send_json(500, {'error': str(exc)})
            return
        if HOSTED and route in ('/api/people', '/firma.png') and not self.authorized():
            self.send_json(401, {'error': 'Clave de acceso incorrecta o faltante.'})
            return
        if route == '/api/people':
            try:
                if HOSTED and not HOSTED_SCTR:
                    self.send_json(200, {'people': [], 'bosses': BOSSES, 'roles': ROLES})
                    return
                people = current_records()
                self.send_json(200, {'people': people, 'bosses': BOSSES, 'roles': ROLES})
            except Exception as exc:
                self.send_json(400, {'error': str(exc)})
            return
        if route == '/firma.png':
            target = app_root() / 'assets' / 'firma.png'
            data = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
            return
        if route in ('/', '/index.html'):
            target = app_root() / 'web' / 'index.html'
            data = target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)
            return
        self.send_json(404, {'error': 'Ruta no encontrada.'})

    def do_POST(self):
        route = urlparse(self.path).path
        if route == '/api/approval/login':
            if not self.authorized():
                self.send_json(401, {'error': 'Ingresa primero con la clave de acceso del personal.'})
                return
            try:
                payload = self.read_json()
                expected = os.environ.get('TREMOND_APPROVAL_PASSWORD', '')
                password = str(payload.get('password', ''))
                role = str(payload.get('role', ''))
                if role not in ('Gerencia', 'Recursos Humanos'):
                    raise ValueError('Selecciona Gerencia o Recursos Humanos.')
                if not expected:
                    self.send_json(503, {'error': 'La validación aún no está configurada en el servidor.'})
                    return
                if not secrets.compare_digest(password, expected):
                    self.send_json(401, {'error': 'Contraseña incorrecta.'})
                    return
                token = secrets.token_urlsafe(32)
                with APPROVAL_SESSIONS_LOCK:
                    APPROVAL_SESSIONS[token] = {'role': role, 'expires': time.time() + 8 * 60 * 60}
                cookie = f'tremond_approval={token}; HttpOnly; Path=/; SameSite=Strict; Max-Age=28800' + ('; Secure' if HOSTED else '')
                self.send_json(200, {'role': role}, {'Set-Cookie': cookie})
            except Exception as exc:
                self.send_json(400, {'error': str(exc)})
            return
        if route == '/api/approval/logout':
            if not self.authorized():
                self.send_json(401, {'error': 'Clave de acceso incorrecta o faltante.'})
                return
            cookie = self.headers.get('Cookie', '')
            token = next((part.strip().split('=', 1)[1] for part in cookie.split(';')
                          if part.strip().startswith('tremond_approval=')), '')
            with APPROVAL_SESSIONS_LOCK:
                APPROVAL_SESSIONS.pop(token, None)
            self.send_json(200, {'message': 'Sesión cerrada.'}, {'Set-Cookie': 'tremond_approval=; HttpOnly; Path=/; SameSite=Strict; Max-Age=0'})
            return
        if route == '/api/approval/decision':
            if not self.authorized():
                self.send_json(401, {'error': 'Clave de acceso incorrecta o faltante.'})
                return
            session = approval_session(self)
            if not session:
                self.send_json(401, {'error': 'Inicia sesión en Validación e historial.'})
                return
            try:
                payload = self.read_json()
                request_id = clean_text(payload.get('request_id'), 80)
                decision = str(payload.get('decision', ''))
                if decision not in ('Aprobado', 'Denegado'):
                    raise ValueError('Selecciona Aprobado o Denegado.')
                column = 'management_status' if session['role'] == 'Gerencia' else 'hr_status'
                timestamp_column = 'management_at' if session['role'] == 'Gerencia' else 'hr_at'
                now = iso_now()
                with closing(db_connect()) as connection, connection:
                    cursor = connection.execute(
                        f'UPDATE overtime_requests SET {column} = ?, {timestamp_column} = ? WHERE request_id = ?',
                        (decision, now, request_id),
                    )
                    if cursor.rowcount != 1:
                        raise ValueError('No se encontró la solicitud.')
                    connection.execute(
                        'INSERT INTO overtime_events(request_id, reviewer_role, decision, created_at) VALUES (?, ?, ?, ?)',
                        (request_id, session['role'], decision, now),
                    )
                self.send_json(200, {'message': 'Validación guardada.', 'requests': list_requests()})
            except Exception as exc:
                self.send_json(400, {'error': str(exc)})
            return
        if route == '/api/sctr':
            if not self.authorized():
                self.send_json(401, {'error': 'Clave de acceso incorrecta o faltante.'})
                return
            try:
                request = self.read_json()
                encoded = str(request.get('data', '')).split(',', 1)[-1]
                raw = base64.b64decode(encoded, validate=True)
                if not raw.startswith(b'%PDF-'):
                    raise ValueError('Selecciona un archivo PDF válido.')
                if HOSTED:
                    people = parse_pdf_bytes(raw)
                    global HOSTED_SCTR
                    HOSTED_SCTR = raw
                else:
                    target = records_file()
                    candidate = target.with_suffix('.pending.pdf')
                    candidate.write_bytes(raw)
                    try:
                        people = parse_sctr(candidate)
                        candidate.replace(target)
                    except Exception:
                        candidate.unlink(missing_ok=True)
                        raise
                self.send_json(200, {'people': people, 'message': f'SCTR actualizado: {len(people)} técnicos.'})
            except Exception as exc:
                self.send_json(400, {'error': str(exc)})
            return
        if route == '/api/generate':
            if not self.authorized():
                self.send_json(401, {'error': 'Clave de acceso incorrecta o faltante.'})
                return
            try:
                fields = self.read_json()
                people = current_records()
                person = next((p for p in people if p['name'] == fields.get('worker')), None)
                if not person:
                    raise ValueError('El técnico ya no está en el SCTR actual. Actualiza la lista y vuelve a seleccionarlo.')
                if fields.get('boss') not in BOSSES:
                    raise ValueError('Selecciona un jefe solicitante de la lista.')
                if fields.get('role') not in ROLES:
                    raise ValueError('Selecciona un cargo de la lista.')
                if fields.get('day_type') not in DAY_TYPES:
                    raise ValueError('Selecciona el tipo de día.')
                overtime_date = datetime.strptime(fields.get('overtime_date', ''), '%Y-%m-%d').date()
                request_date = datetime.strptime(fields.get('request_date', ''), '%Y-%m-%d').date()
                start_min = scheduled_start(overtime_date, person['name'])
                end_min = int(fields.get('end_minutes', -1))
                if not start_min < end_min <= 24 * 60:
                    raise ValueError('La hora de término debe ser posterior a la hora de salida programada.')
                reason = clean_text(fields.get('reason'), 500)
                justification = clean_text(fields.get('justification'), 500)
                if not reason or not justification:
                    raise ValueError('Completa el motivo y la justificación operativa.')
                fields.update({
                    'request_date': request_date.strftime('%d/%m/%Y'),
                    'overtime_date': overtime_date.strftime('%d/%m/%Y'),
                    'worker': person['name'],
                    'dni': person['dni'],
                    'start_time': fmt_time(start_min),
                    'end_time': fmt_time(end_min),
                    'duration': f'{(end_min-start_min)//60:02d} h {(end_min-start_min)%60:02d} min',
                    'reason': reason,
                    'justification': justification,
                })
                temp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
                temp.close()
                generate_pdf(fields, temp.name)
                data = Path(temp.name).read_bytes()
                Path(temp.name).unlink(missing_ok=True)
                request_id = save_request(fields)
                filename = f"Solicitud_Sobretiempo_{overtime_date.strftime('%Y%m%d')}_{re.sub('[^A-Za-z0-9]+','_',person['name']).strip('_')}.pdf"
                self.send_response(200)
                self.send_header('Content-Type', 'application/pdf')
                self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
                self.send_header('X-Request-Id', request_id)
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(data)
            except Exception as exc:
                self.send_json(400, {'error': str(exc)})
            return
        if route == '/api/close':
            if HOSTED:
                self.send_json(404, {'error': 'Ruta no encontrada.'})
                return
            self.send_json(200, {'message': 'Puedes cerrar esta pestaña.'})
            threading.Thread(target=self.server.shutdown, daemon=True).start()
            return
        self.send_json(404, {'error': 'Ruta no encontrada.'})


def main():
    httpd = None
    try:
        bind_host = '0.0.0.0' if HOSTED else '127.0.0.1'
        bind_port = int(os.environ.get('PORT', '0')) if HOSTED else 0
        httpd = ThreadingHTTPServer((bind_host, bind_port), Handler)
        httpd.daemon_threads = True
        url = f'http://127.0.0.1:{httpd.server_port}/'
        log = user_data_dir() / 'webapp.log'
        with log.open('a', encoding='utf-8') as stream:
            stream.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} Abriendo {url}\n')
        if not HOSTED and os.environ.get('TREMOND_NO_OPEN') != '1':
            threading.Timer(0.65, lambda: webbrowser.open_new(url)).start()
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        log = user_data_dir() / 'webapp.log'
        with log.open('a', encoding='utf-8') as stream:
            stream.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} Error: {exc!r}\n')
        try:
            import tkinter as tk
            from tkinter import messagebox
            root = tk.Tk()
            root.withdraw()
            messagebox.showerror('Solicitud de Sobretiempo', f'No se pudo abrir la página web local.\n\n{exc}')
            root.destroy()
        except Exception:
            pass
    finally:
        if httpd:
            httpd.server_close()


if __name__ == '__main__':
    main()
