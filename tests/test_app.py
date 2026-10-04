import json
from pathlib import Path
import secrets
import sqlite3
import tempfile
import unittest
from contextlib import closing
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from app import create_app


class AppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = {'TESTING': True, 'DATABASE': str(Path(self.temp.name) / 'words.sqlite3'),
                       'SECRET_KEY': secrets.token_hex(32)}
        self.app = create_app(self.config)
        self.password = secrets.token_urlsafe(24)
        with closing(sqlite3.connect(self.config['DATABASE'])) as connection:
            with connection:
                for username in ('alice', 'bob'):
                    connection.execute('INSERT INTO users(username,password_hash) VALUES (?,?)',
                                       (username, generate_password_hash(self.password)))
        self.alice, self.bob = self.app.test_client(), self.app.test_client()
        self.csrf = {}
        for client, name in ((self.alice, 'alice'), (self.bob, 'bob')):
            response = client.post('/api/login', json={'username': name, 'password': self.password})
            self.assertEqual(response.status_code, 200)
            self.csrf[id(client)] = response.json['csrf']

    def tearDown(self):
        self.temp.cleanup()

    def send(self, path, payload=None, method='POST', client=None):
        client = client or self.alice
        return client.open('/api/' + path, method=method, json=payload,
                           headers={'X-CSRF-Token': self.csrf[id(client)]})

    def batch(self, name='第一批', **kwargs):
        response = self.send('batches', {'name': name, **kwargs})
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def word(self, batch_id, english='apple', **kwargs):
        response = self.send('words', {'batch_id': batch_id, 'english': english,
                                      'meaning': '苹果', 'phonetic': '/ˈæpəl/', **kwargs})
        self.assertEqual(response.status_code, 201)
        return response.json['id']

    def question(self, mode='study'):
        response = self.send('question', {'mode': mode})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('english', response.json)
        return response.json['id']

    def answer(self, question_id, answer='wrong'):
        response = self.send('answer', {'id': question_id, 'answer': answer})
        self.assertEqual(response.status_code, 200)
        return response.json

    def test_login_cookie_csrf_and_logout(self):
        stranger = self.app.test_client()
        self.assertEqual(stranger.get('/api/batches').status_code, 401)
        self.assertEqual(self.alice.post('/api/batches', json={'name': 'bad'}).status_code, 403)
        cookie = self.alice.get_cookie('words_session')
        self.assertTrue(cookie.http_only)
        self.assertEqual(cookie.same_site, 'Strict')
        self.batch()
        self.assertEqual(self.send('logout', {}).status_code, 200)
        self.assertEqual(self.alice.get('/api/me').status_code, 401)
        response = self.alice.post('/api/login', json={'username': 'alice', 'password': self.password})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.alice.get('/api/batches').json), 1)

    def test_cross_user_isolation_for_every_resource(self):
        batch_id = self.batch()
        word_id = self.word(batch_id)
        question_id = self.question()
        self.assertEqual(self.send('answer', {'id': question_id, 'answer': 'apple'}, client=self.bob).status_code, 404)
        self.answer(question_id)
        for path in ('batches', 'mistakes'):
            self.assertEqual(self.bob.get('/api/' + path).json, [])
        self.assertEqual(self.bob.get('/api/history').json['total'], 0)
        self.assertEqual(self.bob.get('/api/export').json['words'], [])
        self.assertEqual(self.bob.get(f'/api/words?batch_id={batch_id}').status_code, 404)
        for path in (f'batches/{batch_id}', f'words/{word_id}', f'mistakes/{word_id}'):
            self.assertEqual(self.send(path, method='DELETE', client=self.bob).status_code, 404)
        self.assertEqual(self.send(f'batches/{batch_id}', {'name': 'hack'}, 'PATCH', self.bob).status_code, 404)
        self.assertEqual(self.send(f'words/{word_id}', {'english': 'hack'}, 'PATCH', self.bob).status_code, 404)
        self.assertEqual(self.send('words', {'batch_id': batch_id, 'english': 'hack'}, client=self.bob).status_code, 404)
        self.assertEqual(self.send('import', {'batch_id': batch_id, 'csv': 'english,meaning,phonetic'}, client=self.bob).status_code, 404)
        self.assertEqual(self.send('question', {'mode': 'study'}, client=self.bob).status_code, 409)

    def test_spelling_mistakes_and_no_duplicate_answer(self):
        self.word(self.batch())
        question_id = self.question()
        result = self.answer(question_id, ' APPLE ')
        self.assertTrue(result['correct'])
        self.assertEqual(self.send('answer', {'id': question_id, 'answer': 'wrong'}).status_code, 409)
        self.assertEqual(self.alice.get('/api/mistakes').json, [])
        self.assertEqual(self.send('question', {'mode': 'mistakes'}).status_code, 409)
        self.answer(self.question(), 'aple')
        self.assertEqual(len(self.alice.get('/api/mistakes').json), 1)
        self.assertTrue(self.answer(self.question('mistakes'), 'apple')['correct'])
        mistake = self.alice.get('/api/mistakes').json[0]
        self.assertEqual(mistake['correct_count'], 1)
        self.assertEqual(self.alice.get('/api/history').json['total'], 3)
        self.send(f"mistakes/{mistake['id']}", method='DELETE')
        self.assertEqual(self.alice.get('/api/mistakes').json, [])
        self.answer(self.question(), 'nope')
        self.assertEqual(self.alice.get('/api/mistakes').json[0]['wrong_count'], 2)

    def test_original_batch_survives_move_rename_delete(self):
        original = self.batch('首次导入')
        word_id = self.word(original)
        self.answer(self.question())
        target = self.batch('后来移动')
        self.send(f'words/{word_id}', {'batch_id': target}, 'PATCH')
        self.send(f'batches/{original}', {'name': '已改名'}, 'PATCH')
        self.send(f'batches/{original}', method='DELETE')
        self.send(f'batches/{target}', method='DELETE')
        self.assertEqual(self.send('question', {'mode': 'study'}).status_code, 409)
        response = self.send('question', {'mode': 'mistakes'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['original_batch_name'], '首次导入')
        self.assertEqual(self.alice.get('/api/mistakes').json[0]['original_batch_id'], original)
        self.send(f'batches/{original}', {'mistake_weight': 0}, 'PATCH')
        self.assertEqual(self.send('question', {'mode': 'mistakes'}).status_code, 409)

    def test_batch_weight_not_multiplied_by_word_count(self):
        first, second = self.batch('one', study_weight=2), self.batch('two', study_weight=7)
        self.word(first)
        for english in ('book', 'pear', 'cat'):
            self.word(second, english)
        with patch('app.secrets.SystemRandom') as rng:
            rng.return_value.choices.side_effect = lambda groups, weights, k: [groups[0]]
            self.question()
            groups = rng.return_value.choices.call_args.args[0]
            weights = rng.return_value.choices.call_args.kwargs['weights']
            self.assertEqual(sorted(weights), [2, 7])
            self.assertEqual(sorted(len(group) for group in groups), [1, 3])
        self.send(f'batches/{first}', {'study_weight': 0}, 'PATCH')
        self.send(f'batches/{second}', {'study_weight': 0}, 'PATCH')
        self.assertEqual(self.send('question', {'mode': 'study'}).status_code, 409)

    def test_import_validation_is_atomic_and_search_works(self):
        batch_id = self.batch()
        result = self.send('import', {'batch_id': batch_id, 'csv': 'english,meaning,phonetic\napple,苹果,/a/\nbad,,/b/'})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.alice.get(f'/api/words?batch_id={batch_id}').json, [])
        result = self.send('import', {'batch_id': batch_id, 'csv': '\ufeffenglish,meaning,phonetic\napple,"苹果,水果",/a/\nbook,书,/b/'})
        self.assertEqual(result.json['count'], 2)
        rows = self.alice.get(f'/api/words?batch_id={batch_id}&q=BOOK').json
        self.assertEqual(len(rows), 1)
        self.send(f"words/{rows[0]['id']}", {'english': 'books'}, 'PATCH')
        self.assertEqual(self.alice.get(f'/api/words?batch_id={batch_id}&q=books').json[0]['english'], 'books')

    def test_persistence_across_app_restart_and_backup(self):
        batch_id = self.batch(study_weight=3, mistake_weight=8)
        self.word(batch_id)
        self.answer(self.question())
        backup_path = str(Path(self.temp.name) / 'backup.sqlite3')
        result = self.app.test_cli_runner().invoke(args=['backup', backup_path])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertNotEqual(self.app.test_cli_runner().invoke(args=['backup', backup_path]).exit_code, 0)
        for path in (self.config['DATABASE'], backup_path):
            restarted = create_app({**self.config, 'DATABASE': path}).test_client()
            login = restarted.post('/api/login', json={'username': 'alice', 'password': self.password})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(restarted.get('/api/batches').json[0]['mistake_weight'], 8)
            self.assertEqual(len(restarted.get('/api/mistakes').json), 1)
            self.assertEqual(restarted.get('/api/history').json['total'], 1)
            self.assertEqual(len(restarted.get('/api/export').json['words']), 1)

    def test_account_commands_and_session_revocation(self):
        runner = self.app.test_cli_runner()
        new_password = secrets.token_urlsafe(24)
        result = runner.invoke(args=['create-user', 'charlie'], input=f'{new_password}\n{new_password}\n')
        self.assertEqual(result.exit_code, 0, result.output)
        result = runner.invoke(args=['reset-password', 'alice'], input=f'{new_password}\n{new_password}\n')
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.alice.get('/api/me').status_code, 401)
        self.assertEqual(self.alice.post('/api/login', json={'username': 'alice', 'password': self.password}).status_code, 401)
        self.assertEqual(self.alice.post('/api/login', json={'username': 'alice', 'password': new_password}).status_code, 200)

    def test_automatic_local_secret_and_existing_session_survive_restart(self):
        database = str(Path(self.temp.name) / 'automatic' / 'words.sqlite3')
        config = {'TESTING': True, 'DATABASE': database, 'SECRET_KEY': None}
        first_app = create_app(config)
        key_path = Path(database).parent / 'secret.key'
        secret = key_path.read_text()
        self.assertGreaterEqual(len(secret), 32)
        password = secrets.token_urlsafe(24)
        result = first_app.test_cli_runner().invoke(args=['create-user', 'test'], input=f'{password}\n{password}\n')
        self.assertEqual(result.exit_code, 0)
        first_client = first_app.test_client()
        self.assertEqual(first_client.post('/api/login', json={'username': 'test', 'password': password}).status_code, 200)
        token = first_client.get_cookie('words_session').value
        second_app = create_app(config)
        self.assertEqual(key_path.read_text(), secret)
        second_client = second_app.test_client()
        second_client.set_cookie('words_session', token)
        self.assertEqual(second_client.get('/api/me').status_code, 200)

    def test_validation_rate_limit_and_security_headers(self):
        for value in (-1, 1001, 'NaN', 'Infinity', True, None):
            self.assertEqual(self.send('batches', {'name': 'x', 'study_weight': value}).status_code, 400)
        self.assertEqual(self.send('question', {'mode': 'bad'}).status_code, 400)
        self.assertEqual(self.send('batches', {'name': ''}).status_code, 400)
        stranger = self.app.test_client()
        for i in range(10):
            self.assertEqual(stranger.post('/api/login', json={'username': 'alice', 'password': self.password + '!'}).status_code, 401)
        self.assertEqual(stranger.post('/api/login', json={'username': 'alice', 'password': self.password}).status_code, 429)
        response = self.alice.get('/api/export')
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        self.assertNotIn('password_hash', json.dumps(response.json))
        self.assertNotIn('token_hash', json.dumps(response.json))
        with self.alice.get('/') as page:
            self.assertEqual(page.status_code, 200)
        with self.alice.get('/static/app.js') as script:
            self.assertEqual(script.status_code, 200)


if __name__ == '__main__':
    unittest.main()
