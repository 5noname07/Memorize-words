PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    csrf TEXT NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS batches (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    name TEXT NOT NULL,
    study_weight REAL NOT NULL DEFAULT 1 CHECK(study_weight >= 0),
    mistake_weight REAL NOT NULL DEFAULT 1 CHECK(mistake_weight >= 0),
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS words (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    batch_id INTEGER NOT NULL REFERENCES batches(id),
    original_batch_id INTEGER NOT NULL REFERENCES batches(id),
    original_batch_name TEXT NOT NULL,
    english TEXT NOT NULL,
    meaning TEXT NOT NULL,
    phonetic TEXT NOT NULL,
    deleted INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS mistakes (
    word_id INTEGER PRIMARY KEY REFERENCES words(id),
    user_id INTEGER NOT NULL REFERENCES users(id),
    wrong_count INTEGER NOT NULL DEFAULT 1,
    correct_count INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    last_wrong_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS questions (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    word_id INTEGER NOT NULL REFERENCES words(id),
    mode TEXT NOT NULL CHECK(mode IN ('study','mistakes')),
    english TEXT NOT NULL,
    meaning TEXT NOT NULL,
    phonetic TEXT NOT NULL,
    original_batch_name TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    answered INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS attempts (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    word_id INTEGER NOT NULL REFERENCES words(id),
    question_id TEXT NOT NULL UNIQUE REFERENCES questions(id),
    mode TEXT NOT NULL,
    answer TEXT NOT NULL,
    expected TEXT NOT NULL,
    meaning TEXT NOT NULL,
    original_batch_name TEXT NOT NULL,
    correct INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS login_limits (
    key TEXT PRIMARY KEY,
    failures INTEGER NOT NULL,
    window_start INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS words_owner ON words(user_id, deleted, batch_id);
CREATE INDEX IF NOT EXISTS attempts_owner ON attempts(user_id, id DESC);
CREATE INDEX IF NOT EXISTS sessions_expiry ON sessions(expires_at);
