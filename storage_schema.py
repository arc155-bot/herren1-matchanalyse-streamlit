from __future__ import annotations

import os
from pathlib import Path
import sqlite3


DEFAULT_DB = Path(
    os.environ.get(
        "H1_MATCHANALYSE_DB",
        Path(__file__).with_name("daten") / "herren1_matchanalyse.db",
    )
)
SCHEMA_VERSION = 5


def connect(db_path: Path = DEFAULT_DB) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout = 5000")
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _ensure_column(connection: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db(db_path: Path = DEFAULT_DB) -> None:
    with connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS linked_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                focus TEXT NOT NULL,
                source_url TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS training_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                training_date TEXT NOT NULL,
                primary_focus TEXT NOT NULL,
                secondary_focus TEXT NOT NULL,
                attendance INTEGER NOT NULL,
                setter_present INTEGER NOT NULL,
                hall_minutes INTEGER NOT NULL,
                goal_rating INTEGER NOT NULL,
                load_rating INTEGER NOT NULL,
                keep_note TEXT NOT NULL DEFAULT '',
                change_note TEXT NOT NULL DEFAULT '',
                plan_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS match_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL DEFAULT 0,
                rally_number INTEGER NOT NULL DEFAULT 0,
                sequence_number INTEGER NOT NULL DEFAULT 1,
                match_date TEXT NOT NULL,
                opponent TEXT NOT NULL DEFAULT '',
                set_number INTEGER NOT NULL DEFAULT 1,
                ball_type TEXT NOT NULL,
                server_id TEXT NOT NULL DEFAULT '',
                server_name TEXT NOT NULL DEFAULT '',
                service_type TEXT NOT NULL DEFAULT '',
                service_result TEXT NOT NULL DEFAULT '',
                service_origin_x INTEGER,
                service_origin_y INTEGER,
                receiver_id TEXT NOT NULL,
                receiver_name TEXT NOT NULL,
                first_contact_quality TEXT NOT NULL,
                first_contact_x INTEGER,
                first_contact_y INTEGER,
                first_contact_too_low INTEGER NOT NULL DEFAULT 0,
                no_contact_reason TEXT NOT NULL DEFAULT '',
                communication_player_ids_json TEXT NOT NULL DEFAULT '[]',
                communication_player_names_json TEXT NOT NULL DEFAULT '[]',
                setter_involved INTEGER NOT NULL DEFAULT 0,
                setter_id TEXT NOT NULL DEFAULT '',
                setter_name TEXT NOT NULL DEFAULT '',
                setter_movement TEXT NOT NULL DEFAULT '',
                set_quality TEXT NOT NULL DEFAULT '',
                set_tendency TEXT NOT NULL DEFAULT '',
                set_inside_meters REAL NOT NULL DEFAULT 0,
                set_origin TEXT NOT NULL DEFAULT '',
                set_origin_x REAL,
                set_origin_y REAL,
                attacker_id TEXT NOT NULL DEFAULT '',
                attacker_name TEXT NOT NULL DEFAULT '',
                attack_type TEXT NOT NULL DEFAULT '',
                attack_result TEXT NOT NULL DEFAULT '',
                attack_block_outcome TEXT NOT NULL DEFAULT '',
                attack_origin TEXT NOT NULL DEFAULT '',
                landing_x INTEGER,
                landing_y INTEGER,
                landing_out INTEGER NOT NULL DEFAULT 0,
                opponent_attack_origin TEXT NOT NULL DEFAULT '',
                block_player_id TEXT NOT NULL DEFAULT '',
                block_player_name TEXT NOT NULL DEFAULT '',
                block_result TEXT NOT NULL DEFAULT '',
                block_formation TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS match_sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_date TEXT NOT NULL,
                opponent TEXT NOT NULL,
                video_url TEXT NOT NULL DEFAULT '',
                video_title TEXT NOT NULL DEFAULT '',
                lineup_json TEXT NOT NULL DEFAULT '[]',
                state_json TEXT NOT NULL DEFAULT '{}',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS match_video_segments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                set_number INTEGER NOT NULL,
                rally_number INTEGER NOT NULL,
                start_seconds INTEGER NOT NULL,
                end_seconds INTEGER NOT NULL,
                winner TEXT NOT NULL DEFAULT '',
                our_score INTEGER NOT NULL DEFAULT 0,
                opponent_score INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(session_id, set_number, rally_number)
            );

            CREATE TABLE IF NOT EXISTS match_video_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                video_seconds INTEGER NOT NULL,
                set_number INTEGER NOT NULL,
                rally_number INTEGER NOT NULL,
                our_score INTEGER NOT NULL DEFAULT 0,
                opponent_score INTEGER NOT NULL DEFAULT 0,
                side TEXT NOT NULL DEFAULT '',
                outgoing_id TEXT NOT NULL DEFAULT '',
                outgoing_name TEXT NOT NULL DEFAULT '',
                incoming_id TEXT NOT NULL DEFAULT '',
                incoming_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS app_metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS custom_exercises (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                accent TEXT NOT NULL DEFAULT '',
                organization TEXT NOT NULL DEFAULT '',
                execution_json TEXT NOT NULL DEFAULT '[]',
                rules_json TEXT NOT NULL DEFAULT '[]',
                scoring TEXT NOT NULL DEFAULT '',
                rotation_json TEXT NOT NULL DEFAULT '[]',
                timeline_json TEXT NOT NULL DEFAULT '[]',
                variations_json TEXT NOT NULL DEFAULT '[]',
                coaching_questions_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                court_json TEXT NOT NULL DEFAULT '[]',
                paths_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS custom_training_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                training_date TEXT NOT NULL,
                attendance_json TEXT NOT NULL DEFAULT '[]',
                notes TEXT NOT NULL DEFAULT '',
                items_json TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        _ensure_column(connection, "match_actions", "session_id", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "match_actions", "rally_number", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "match_actions", "sequence_number", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(connection, "match_actions", "server_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "server_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "service_type", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "service_result", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "service_origin_x", "INTEGER")
        _ensure_column(connection, "match_actions", "service_origin_y", "INTEGER")
        _ensure_column(connection, "match_actions", "block_player_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "block_player_name", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "block_result", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "block_formation", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "attack_block_outcome", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "first_contact_x", "INTEGER")
        _ensure_column(connection, "match_actions", "first_contact_y", "INTEGER")
        _ensure_column(
            connection,
            "match_actions",
            "first_contact_too_low",
            "INTEGER NOT NULL DEFAULT 0",
        )
        _ensure_column(connection, "match_actions", "no_contact_reason", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(
            connection,
            "match_actions",
            "communication_player_ids_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        _ensure_column(
            connection,
            "match_actions",
            "communication_player_names_json",
            "TEXT NOT NULL DEFAULT '[]'",
        )
        _ensure_column(connection, "match_actions", "set_tendency", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "set_inside_meters", "REAL NOT NULL DEFAULT 0")
        _ensure_column(connection, "match_actions", "set_origin", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "set_origin_x", "REAL")
        _ensure_column(connection, "match_actions", "set_origin_y", "REAL")
        _ensure_column(connection, "match_actions", "attack_origin", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_actions", "landing_x", "INTEGER")
        _ensure_column(connection, "match_actions", "landing_y", "INTEGER")
        _ensure_column(connection, "match_actions", "landing_out", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(
            connection,
            "match_actions",
            "opponent_attack_origin",
            "TEXT NOT NULL DEFAULT ''",
        )
        _ensure_column(connection, "match_sessions", "video_url", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_sessions", "video_title", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_video_segments", "winner", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(connection, "match_video_segments", "our_score", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "match_video_segments", "opponent_score", "INTEGER NOT NULL DEFAULT 0")
        _ensure_column(connection, "custom_exercises", "paths_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "custom_exercises", "tags_json", "TEXT NOT NULL DEFAULT '[]'")
        _ensure_column(connection, "training_logs", "custom_plan_id", "INTEGER NOT NULL DEFAULT 0")
        connection.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_match_actions_session_set_rally
                ON match_actions (session_id, set_number, rally_number, sequence_number);
            CREATE INDEX IF NOT EXISTS idx_match_sessions_date
                ON match_sessions (match_date DESC, id DESC);
            CREATE INDEX IF NOT EXISTS idx_match_video_segments_session
                ON match_video_segments (session_id, set_number, rally_number);
            CREATE INDEX IF NOT EXISTS idx_match_video_events_session
                ON match_video_events (session_id, video_seconds, id);
            CREATE INDEX IF NOT EXISTS idx_training_logs_date
                ON training_logs (training_date DESC, id DESC);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_training_logs_custom_plan
                ON training_logs (custom_plan_id) WHERE custom_plan_id > 0;
            """
        )
