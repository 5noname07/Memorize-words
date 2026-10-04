"""Small, persistent vocabulary app. All data access is scoped to the authenticated owner."""
import csv
import hashlib
import hmac
import io
import math
import os
from pathlib import Path
import secrets
import sqlite3
import time
import unicodedata
from contextlib import closing
from functools import wraps

import click
from flask import Flask, g, jsonify, request, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent


def create_app(test_config=None):
    app = Flask(__name__, static_folder='static')
    app.config.update(
        DATABASE=os.environ.get('DATABASE', str(ROOT / 'instance' / 'words.sqlite3')),
        SECRET_KEY=os.environ.get('SECRET_KEY'),
        COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1',
        MAX_CONTENT_LENGTH=2 * 1024 * 1024,
    )
    if test_config:
        app.config.update(test_config)
    database_path = Path(app.config['DATABASE']).resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    app.config['DATABASE'] = str(database_path)
    if not app.config['SECRET_KEY']:
        # The local secret survives restarts and lives next to the ignored database.
        secret_path = database_path.parent / 'secret.key'
        try:
            with secret_path.open('x', encoding='utf-8') as secret_file:
                secret_file.write(secrets.token_hex(32))
        except FileExistsError:
            pass
        app.config['SECRET_KEY'] = secret_path.read_text(encoding='utf-8').strip()
    if len(app.config['SECRET_KEY']) < 32:
        raise RuntimeError('SECRET_KEY must contain at least 32 characters.')

    def token_digest(token):
        return hmac.new(app.config['SECRET_KEY'].encode(), token.encode(), hashlib.sha256).hexdigest()

    def db():
        if 'db' not in g:
            g.db = sqlite3.connect(app.config['DATABASE'], timeout=15)
            g.db.row_factory = sqlite3.Row
            g.db.execute('PRAGMA foreign_keys=ON')
            g.db.execute('PRAGMA journal_mode=WAL')
            g.db.execute('PRAGMA synchronous=FULL')
        return g.db

    @app.teardown_appcontext
    def close_db(error):
        connection = g.pop('db', None)
        if connection:
            connection.close()

    with app.app_context():
        db().executescript((ROOT / 'schema.sql').read_text(encoding='utf-8'))
        db().commit()

    def fail(message, status=400):
        return jsonify(error=message), status

    def authenticated(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            token = request.cookies.get('words_session', '')
            token_hash = token_digest(token)
            row = db().execute(
                'SELECT s.*,u.username FROM sessions s JOIN users u ON u.id=s.user_id '
                'WHERE s.token_hash=? AND s.expires_at>?', (token_hash, int(time.time()))
            ).fetchone()
            if not row:
                return fail('请先登录', 401)
            g.user_id, g.auth = row['user_id'], row
            if request.method not in ('GET', 'HEAD', 'OPTIONS'):
                if not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), row['csrf']):
                    return fail('登录校验失败，请刷新页面', 403)
            return view(*args, **kwargs)
        return wrapped

    def data():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ValueError('请提供有效的 JSON 数据')
        return value

    def text(value, label, limit=200):
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
            raise ValueError(f'{label}不能为空，且最多 {limit} 字符')
        return value.strip()

    def weight(value):
        if isinstance(value, bool):
            raise ValueError('权重必须在 0～1000 之间')
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise ValueError('权重必须在 0～1000 之间')
        if not math.isfinite(number) or not 0 <= number <= 1000:
            raise ValueError('权重必须在 0～1000 之间')
        return number

    def batch(batch_id, active=True):
        if isinstance(batch_id, bool) or not isinstance(batch_id, int) or not 0 < batch_id < 2**63:
            raise ValueError('批次 ID 无效')
        row = db().execute('SELECT * FROM batches WHERE id=? AND user_id=?',
                           (batch_id, g.user_id)).fetchone()
        if not row or (active and row['archived']):
            raise LookupError('批次不存在')
        return row

    def word(word_id):
        row = db().execute('SELECT * FROM words WHERE id=? AND user_id=? AND deleted=0',
                           (word_id, g.user_id)).fetchone()
        if not row:
            raise LookupError('单词不存在')
        return row

    @app.errorhandler(ValueError)
    def invalid(error):
        return fail(str(error))

    @app.errorhandler(LookupError)
    def missing(error):
        return fail(str(error), 404)

    @app.errorhandler(413)
    def too_large(error):
        return fail('请求过大，最多 2 MB', 413)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        if request.path.startswith('/api/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.get('/')
    def index():
        return send_from_directory(app.static_folder, 'index.html')

    @app.post('/api/login')
    def login():
        # Require same-origin JSON; cross-origin HTML forms cannot authenticate.
        payload = data()
        username = text(payload.get('username'), '用户名', 50)
        password = text(payload.get('password'), '密码', 256)
        now = int(time.time())
        keys = ['user:' + username, 'ip:' + (request.remote_addr or '')]
        for key in keys:
            limit = db().execute('SELECT * FROM login_limits WHERE key=?', (key,)).fetchone()
            if limit and now - limit['window_start'] < 900 and limit['failures'] >= 10:
                return fail('登录失败次数过多，请 15 分钟后再试', 429)
        user = db().execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        # Always perform a password hash check to reduce username enumeration timing differences.
        valid = check_password_hash(user['password_hash'] if user else app.config['DUMMY_HASH'], password)
        if not user or not valid:
            with db():
                for key in keys:
                    db().execute(
                        'INSERT INTO login_limits VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET '
                        'failures=CASE WHEN ?-window_start>=900 THEN 1 ELSE failures+1 END, '
                        'window_start=CASE WHEN ?-window_start>=900 THEN ? ELSE window_start END',
                        (key, now, now, now, now))
            return fail('用户名或密码错误', 401)
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with db():
            db().execute('DELETE FROM sessions WHERE expires_at<=?', (now,))
            db().execute('DELETE FROM login_limits WHERE key=?', (keys[0],))
            old = request.cookies.get('words_session')
            if old:
                db().execute('DELETE FROM sessions WHERE token_hash=?',
                             (token_digest(old),))
            db().execute('INSERT INTO sessions VALUES (?,?,?,?)',
                         (token_digest(token), user['id'], csrf, now + 7 * 86400))
        response = jsonify(username=username, csrf=csrf)
        response.set_cookie('words_session', token, max_age=7 * 86400, httponly=True,
                            secure=app.config['COOKIE_SECURE'], samesite='Strict')
        return response

    app.config['DUMMY_HASH'] = generate_password_hash(secrets.token_urlsafe(32))

    @app.get('/api/me')
    @authenticated
    def me():
        return jsonify(username=g.auth['username'], csrf=g.auth['csrf'])

    @app.post('/api/logout')
    @authenticated
    def logout():
        with db():
            db().execute('DELETE FROM sessions WHERE token_hash=?', (g.auth['token_hash'],))
        response = jsonify(ok=True)
        response.delete_cookie('words_session', secure=app.config['COOKIE_SECURE'], samesite='Strict')
        return response

    @app.get('/api/batches')
    @authenticated
    def batches():
        rows = db().execute(
            'SELECT b.*, (SELECT COUNT(*) FROM words w WHERE w.batch_id=b.id AND w.user_id=b.user_id '
            'AND w.deleted=0) AS word_count, (SELECT COUNT(*) FROM mistakes m JOIN words w ON w.id=m.word_id '
            'WHERE w.original_batch_id=b.id AND m.user_id=b.user_id AND m.active=1) AS mistake_count '
            'FROM batches b WHERE b.user_id=? ORDER BY b.id DESC', (g.user_id,)).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.post('/api/batches')
    @authenticated
    def create_batch():
        payload = data()
        with db():
            cursor = db().execute('INSERT INTO batches(user_id,name,study_weight,mistake_weight) VALUES (?,?,?,?)',
                                  (g.user_id, text(payload.get('name'), '批次名', 80),
                                   weight(payload.get('study_weight', 1)), weight(payload.get('mistake_weight', 1))))
        return jsonify(id=cursor.lastrowid), 201

    @app.patch('/api/batches/<int:batch_id>')
    @authenticated
    def update_batch(batch_id):
        row, payload = batch(batch_id, False), data()
        with db():
            db().execute('UPDATE batches SET name=?,study_weight=?,mistake_weight=? WHERE id=? AND user_id=?',
                         (text(payload.get('name', row['name']), '批次名', 80),
                          weight(payload.get('study_weight', row['study_weight'])),
                          weight(payload.get('mistake_weight', row['mistake_weight'])), batch_id, g.user_id))
        return jsonify(ok=True)

    @app.delete('/api/batches/<int:batch_id>')
    @authenticated
    def delete_batch(batch_id):
        batch(batch_id)
        with db():
            db().execute('UPDATE batches SET archived=1 WHERE id=? AND user_id=?', (batch_id, g.user_id))
            db().execute('UPDATE words SET deleted=1 WHERE batch_id=? AND user_id=?', (batch_id, g.user_id))
        return jsonify(ok=True)

    @app.get('/api/words')
    @authenticated
    def words():
        batch_id = request.args.get('batch_id', type=int)
        batch(batch_id)
        query = request.args.get('q', '').strip().casefold()
        rows = db().execute('SELECT * FROM words WHERE user_id=? AND batch_id=? AND deleted=0 ORDER BY id DESC',
                            (g.user_id, batch_id)).fetchall()
        return jsonify([dict(r) for r in rows if query in r['english'].casefold() or query in r['meaning'].casefold()])

    def validated_word(payload):
        return (text(payload.get('english'), '英文', 100), text(payload.get('meaning'), '释义', 500),
                text(payload.get('phonetic'), '音标', 150))

    def insert_word(batch_row, fields):
        return db().execute(
            'INSERT INTO words(user_id,batch_id,original_batch_id,original_batch_name,english,meaning,phonetic) '
            'VALUES (?,?,?,?,?,?,?)',
            (g.user_id, batch_row['id'], batch_row['id'], batch_row['name'], *fields)).lastrowid

    @app.post('/api/words')
    @authenticated
    def create_word():
        payload = data()
        row = batch(payload.get('batch_id'))
        fields = validated_word(payload)
        with db():
            word_id = insert_word(row, fields)
        return jsonify(id=word_id), 201

    @app.patch('/api/words/<int:word_id>')
    @authenticated
    def update_word(word_id):
        row, payload = word(word_id), data()
        target = batch(payload.get('batch_id', row['batch_id']))
        fields = validated_word({key: payload.get(key, row[key]) for key in ('english', 'meaning', 'phonetic')})
        with db():
            db().execute('UPDATE words SET english=?,meaning=?,phonetic=?,batch_id=? WHERE id=? AND user_id=?',
                         (*fields, target['id'], word_id, g.user_id))
        return jsonify(ok=True)

    @app.delete('/api/words/<int:word_id>')
    @authenticated
    def delete_word(word_id):
        word(word_id)
        with db():
            db().execute('UPDATE words SET deleted=1 WHERE id=? AND user_id=?', (word_id, g.user_id))
        return jsonify(ok=True)

    @app.post('/api/import')
    @authenticated
    def import_words():
        payload = data()
        row = batch(payload.get('batch_id'))
        source = text(payload.get('csv'), 'CSV', 1000000).lstrip('\ufeff')
        reader = csv.DictReader(io.StringIO(source))
        if not reader.fieldnames or not {'english', 'meaning', 'phonetic'}.issubset(reader.fieldnames):
            return fail('CSV 表头必须包含 english,meaning,phonetic')
        records = []
        for number, item in enumerate(reader, 2):
            try:
                records.append(validated_word(item))
            except ValueError as error:
                return fail(f'第 {number} 行：{error}；本次未导入任何单词')
            if len(records) > 5000:
                return fail('每次最多导入 5000 个单词')
        if not records:
            return fail('CSV 中没有单词')
        with db():
            for fields in records:
                insert_word(row, fields)
        return jsonify(count=len(records)), 201

    @app.get('/api/mistakes')
    @authenticated
    def mistakes():
        rows = db().execute(
            'SELECT w.*,m.wrong_count,m.correct_count,m.last_wrong_at FROM mistakes m JOIN words w ON w.id=m.word_id '
            'WHERE m.user_id=? AND w.user_id=? AND m.active=1 ORDER BY m.last_wrong_at DESC,w.id DESC',
            (g.user_id, g.user_id)).fetchall()
        return jsonify([dict(row) for row in rows])

    @app.delete('/api/mistakes/<int:word_id>')
    @authenticated
    def remove_mistake(word_id):
        with db():
            cursor = db().execute('UPDATE mistakes SET active=0 WHERE word_id=? AND user_id=? AND active=1',
                                  (word_id, g.user_id))
            if not cursor.rowcount:
                raise LookupError('错题不存在')
        return jsonify(ok=True)

    @app.post('/api/question')
    @authenticated
    def question():
        mode = data().get('mode')
        if mode not in ('study', 'mistakes'):
            return fail('练习模式无效')
        if mode == 'study':
            candidates = db().execute(
                'SELECT w.*, b.study_weight AS weight,b.id AS group_id FROM words w JOIN batches b ON b.id=w.batch_id '
                'WHERE w.user_id=? AND b.user_id=? AND w.deleted=0 AND b.archived=0 AND b.study_weight>0',
                (g.user_id, g.user_id)).fetchall()
        else:
            candidates = db().execute(
                'SELECT w.*, b.mistake_weight AS weight,b.id AS group_id FROM mistakes m '
                'JOIN words w ON w.id=m.word_id JOIN batches b ON b.id=w.original_batch_id '
                'WHERE m.user_id=? AND w.user_id=? AND b.user_id=? AND m.active=1 AND b.mistake_weight>0',
                (g.user_id, g.user_id, g.user_id)).fetchall()
        groups = {}
        for row in candidates:
            groups.setdefault(row['group_id'], []).append(row)
        if not groups:
            return fail('没有可练习的单词，请添加单词或将相应批次的权重设为大于 0', 409)
        # Choose a batch by weight, THEN uniformly choose one word within it.
        group = secrets.SystemRandom().choices(list(groups.values()),
                    weights=[items[0]['weight'] for items in groups.values()], k=1)[0]
        selected = secrets.choice(group)
        question_id = secrets.token_urlsafe(24)
        now = int(time.time())
        with db():
            db().execute('DELETE FROM questions WHERE answered=0 AND created_at<?', (now - 86400,))
            db().execute('INSERT INTO questions(id,user_id,word_id,mode,english,meaning,phonetic,original_batch_name,created_at) '
                         'VALUES (?,?,?,?,?,?,?,?,?)', (question_id, g.user_id, selected['id'], mode, selected['english'],
                         selected['meaning'], selected['phonetic'], selected['original_batch_name'], now))
        return jsonify(id=question_id, meaning=selected['meaning'], phonetic=selected['phonetic'],
                       original_batch_name=selected['original_batch_name'])

    @app.post('/api/answer')
    @authenticated
    def answer():
        payload = data()
        question_id = text(payload.get('id'), '题目 ID', 100)
        entered = payload.get('answer', '')
        if not isinstance(entered, str) or len(entered) > 200:
            return fail('答案最多 200 字符')
        # Serialize answer consumption so duplicate submissions cannot create duplicate records.
        connection = db()
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute('SELECT * FROM questions WHERE id=? AND user_id=?',
                                      (question_id, g.user_id)).fetchone()
            if not row:
                return fail('题目不存在', 404)
            if row['answered']:
                return fail('此题已提交，请开始下一题', 409)
            if int(time.time()) - row['created_at'] >= 86400:
                return fail('题目已过期，请开始下一题', 409)
            normalize = lambda value: unicodedata.normalize('NFKC', value).strip().casefold()
            correct = normalize(entered) == normalize(row['english'])
            connection.execute('UPDATE questions SET answered=1 WHERE id=? AND user_id=?', (row['id'], g.user_id))
            connection.execute('INSERT INTO attempts(user_id,word_id,question_id,mode,answer,expected,meaning,original_batch_name,correct) '
                               'VALUES (?,?,?,?,?,?,?,?,?)', (g.user_id, row['word_id'], row['id'], row['mode'], entered,
                               row['english'], row['meaning'], row['original_batch_name'], int(correct)))
            if not correct:
                connection.execute('INSERT INTO mistakes(word_id,user_id) VALUES (?,?) ON CONFLICT(word_id) DO UPDATE SET '
                                   'wrong_count=wrong_count+1,active=1,last_wrong_at=CURRENT_TIMESTAMP',
                                   (row['word_id'], g.user_id))
            elif row['mode'] == 'mistakes':
                connection.execute('UPDATE mistakes SET correct_count=correct_count+1 WHERE word_id=? AND user_id=?',
                                   (row['word_id'], g.user_id))
        return jsonify(correct=correct, expected=row['english'])

    @app.get('/api/history')
    @authenticated
    def history():
        rows = db().execute('SELECT * FROM attempts WHERE user_id=? ORDER BY id DESC LIMIT 100', (g.user_id,)).fetchall()
        totals = db().execute('SELECT COUNT(*) AS total,COALESCE(SUM(correct),0) AS correct FROM attempts WHERE user_id=?',
                              (g.user_id,)).fetchone()
        return jsonify(items=[dict(row) for row in rows], total=totals['total'], correct=totals['correct'])

    @app.get('/api/export')
    @authenticated
    def export():
        # Personal portable backup, never includes password hashes or session tokens.
        result = {'format_version': 1, 'username': g.auth['username']}
        for table in ('batches', 'words', 'mistakes', 'attempts'):
            result[table] = [dict(row) for row in db().execute(f'SELECT * FROM {table} WHERE user_id=?', (g.user_id,))]
        response = jsonify(result)
        response.headers['Content-Disposition'] = 'attachment; filename="vocabulary-backup.json"'
        return response

    @app.cli.command('create-user')
    @click.argument('username')
    @click.password_option(confirmation_prompt=True)
    def create_user(username, password):
        """Create a private account; passwords are entered interactively."""
        username = text(username, '用户名', 50)
        if len(password) < 10 or len(password) > 256 or password != password.strip():
            raise click.ClickException('密码须为 10～256 字符，首尾不能有空格')
        try:
            with db():
                db().execute('INSERT INTO users(username,password_hash) VALUES (?,?)',
                             (username, generate_password_hash(password)))
        except sqlite3.IntegrityError:
            raise click.ClickException('用户名已存在')
        click.echo(f'已创建用户：{username}')

    @app.cli.command('reset-password')
    @click.argument('username')
    @click.password_option(confirmation_prompt=True)
    def reset_password(username, password):
        """Reset one user's password and revoke all of their sessions."""
        if len(password) < 10 or len(password) > 256 or password != password.strip():
            raise click.ClickException('密码须为 10～256 字符，首尾不能有空格')
        row = db().execute('SELECT id FROM users WHERE username=?', (username,)).fetchone()
        if not row:
            raise click.ClickException('用户不存在')
        with db():
            db().execute('UPDATE users SET password_hash=? WHERE id=?', (generate_password_hash(password), row['id']))
            db().execute('DELETE FROM sessions WHERE user_id=?', (row['id'],))
        click.echo('密码已重置；该用户所有登录会话已撤销，学习数据保留。')

    @app.cli.command('backup')
    @click.argument('destination', type=click.Path())
    def backup(destination):
        """Create a consistent SQLite backup, including WAL changes."""
        target = Path(destination).resolve()
        if target.exists() or target == database_path:
            raise click.ClickException('目标已存在；请选择新路径，避免覆盖')
        target.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(target)) as output:
            db().backup(output)
        click.echo(f'备份已保存：{target}')

    return app


if __name__ == '__main__':
    from waitress import serve
    serve(create_app(), host=os.environ.get('HOST', '127.0.0.1'), port=int(os.environ.get('PORT', '8000')))
