from __future__ import annotations

from dataclasses import asdict
from datetime import datetime
import json
from pathlib import Path
import sqlite3
from typing import Any

from bundled_coach_data import BUNDLED_CONTENT_MARKER, load_bundled_coach_data
from recovered_exercises import RECOVERED_CUSTOM_EXERCISES
from storage_schema import DEFAULT_DB, connect, init_db


def _json_value(raw_value: Any, fallback: Any) -> Any:
    if raw_value in (None, ""):
        return fallback
    try:
        return json.loads(str(raw_value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return fallback


def _custom_exercise_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    for column in (
        "execution_json",
        "rules_json",
        "rotation_json",
        "timeline_json",
        "variations_json",
        "coaching_questions_json",
        "tags_json",
        "court_json",
        "paths_json",
    ):
        result[column.removesuffix("_json")] = _json_value(result.pop(column, ""), [])
    return result


def save_custom_exercise(
    *,
    title: str,
    accent: str,
    organization: str,
    execution: list[str],
    rules: list[str],
    scoring: str,
    rotation: list[dict[str, Any]],
    timeline: list[dict[str, Any]],
    variations: list[str],
    coaching_questions: list[str],
    court: list[dict[str, Any]],
    paths: list[dict[str, Any]] | None = None,
    tags: list[str] | None = None,
    exercise_id: int | None = None,
    db_path: Path = DEFAULT_DB,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    values = (
        title.strip(),
        accent.strip(),
        organization.strip(),
        json.dumps([value.strip() for value in execution if value.strip()], ensure_ascii=False),
        json.dumps([value.strip() for value in rules if value.strip()], ensure_ascii=False),
        scoring.strip(),
        json.dumps(rotation, ensure_ascii=False),
        json.dumps(timeline, ensure_ascii=False),
        json.dumps([value.strip() for value in variations if value.strip()], ensure_ascii=False),
        json.dumps(
            [value.strip() for value in coaching_questions if value.strip()],
            ensure_ascii=False,
        ),
        json.dumps([value.strip() for value in tags or [] if value.strip()], ensure_ascii=False),
        json.dumps(court, ensure_ascii=False),
        json.dumps(paths or [], ensure_ascii=False),
    )
    with connect(db_path) as connection:
        if exercise_id:
            connection.execute(
                """
                UPDATE custom_exercises
                SET title = ?, accent = ?, organization = ?, execution_json = ?,
                    rules_json = ?, scoring = ?, rotation_json = ?, timeline_json = ?,
                    variations_json = ?, coaching_questions_json = ?, tags_json = ?, court_json = ?,
                    paths_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (*values, now, int(exercise_id)),
            )
            return int(exercise_id)
        cursor = connection.execute(
            """
            INSERT INTO custom_exercises (
                title, accent, organization, execution_json, rules_json, scoring,
                rotation_json, timeline_json, variations_json,
                coaching_questions_json, tags_json, court_json, paths_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, now, now),
        )
        return int(cursor.lastrowid)


def seed_recovered_custom_exercises(db_path: Path = DEFAULT_DB) -> int:
    """Restore the PDF-backed exercises once per database without duplicating them."""
    marker = "recovered_custom_exercises_v1"
    with connect(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM app_metadata WHERE key = ?",
            (marker,),
        ).fetchone():
            return 0
        existing_titles = {
            str(row[0]).strip().casefold()
            for row in connection.execute("SELECT title FROM custom_exercises").fetchall()
        }

    restored = 0
    for exercise in RECOVERED_CUSTOM_EXERCISES:
        if str(exercise["title"]).strip().casefold() in existing_titles:
            continue
        save_custom_exercise(**exercise, db_path=db_path)
        restored += 1

    with connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            (marker, str(restored)),
        )
    return restored


def _backup_exercise_values(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(row.get("title") or ""),
        "accent": str(row.get("accent") or ""),
        "organization": str(row.get("organization") or ""),
        "execution": _json_value(row.get("execution_json"), []),
        "rules": _json_value(row.get("rules_json"), []),
        "scoring": str(row.get("scoring") or ""),
        "rotation": _json_value(row.get("rotation_json"), []),
        "timeline": _json_value(row.get("timeline_json"), []),
        "variations": _json_value(row.get("variations_json"), []),
        "coaching_questions": _json_value(row.get("coaching_questions_json"), []),
        "tags": _json_value(row.get("tags_json"), []),
        "court": _json_value(row.get("court_json"), []),
        "paths": _json_value(row.get("paths_json"), []),
    }


def _remap_plan_exercise_ids(
    items: list[dict[str, Any]],
    exercise_ids_by_title: dict[str, int],
) -> list[dict[str, Any]]:
    remapped = json.loads(json.dumps(items, ensure_ascii=False))
    for item in remapped:
        if not isinstance(item, dict) or int(item.get("exercise_id") or 0) <= 0:
            continue
        snapshot = item.get("exercise")
        snapshot_title = snapshot.get("title") if isinstance(snapshot, dict) else ""
        title_key = str(snapshot_title or item.get("title") or "").strip().casefold()
        current_id = exercise_ids_by_title.get(title_key)
        if current_id is None:
            item["exercise_id"] = 0
            continue
        item["exercise_id"] = current_id
        if isinstance(snapshot, dict):
            snapshot["id"] = current_id
    return remapped


def seed_bundled_coach_data(db_path: Path = DEFAULT_DB) -> dict[str, int]:
    """Merge the shipped exercises and plan once, preserving later user edits."""

    with connect(db_path) as connection:
        if connection.execute(
            "SELECT 1 FROM app_metadata WHERE key = ?",
            (BUNDLED_CONTENT_MARKER,),
        ).fetchone():
            return {"exercises": 0, "training_plans": 0}

    bundled = load_bundled_coach_data()
    with connect(db_path) as connection:
        exercise_ids_by_title = {
            str(row["title"]).strip().casefold(): int(row["id"])
            for row in connection.execute("SELECT id, title FROM custom_exercises").fetchall()
        }

    inserted_exercises = 0
    for row in bundled["custom_exercises"]:
        exercise = _backup_exercise_values(row)
        title_key = exercise["title"].strip().casefold()
        if not title_key or title_key in exercise_ids_by_title:
            continue
        exercise_id = save_custom_exercise(**exercise, db_path=db_path)
        exercise_ids_by_title[title_key] = exercise_id
        inserted_exercises += 1

    with connect(db_path) as connection:
        existing_plans = {
            (str(row["title"]).strip().casefold(), str(row["training_date"]))
            for row in connection.execute("SELECT title, training_date FROM custom_training_plans").fetchall()
        }

    inserted_plans = 0
    for row in bundled["custom_training_plans"]:
        title = str(row.get("title") or "").strip()
        training_date = str(row.get("training_date") or "")
        plan_key = (title.casefold(), training_date)
        if not title or plan_key in existing_plans:
            continue
        raw_items = _json_value(row.get("items_json"), [])
        items = _remap_plan_exercise_ids(raw_items, exercise_ids_by_title)
        save_custom_training_plan(
            title=title,
            training_date=training_date,
            attendance=_json_value(row.get("attendance_json"), []),
            notes=str(row.get("notes") or ""),
            items=items,
            db_path=db_path,
        )
        existing_plans.add(plan_key)
        inserted_plans += 1

    result = {"exercises": inserted_exercises, "training_plans": inserted_plans}
    with connect(db_path) as connection:
        connection.execute(
            "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
            (BUNDLED_CONTENT_MARKER, json.dumps(result, ensure_ascii=False)),
        )
    return result


def list_custom_exercises(db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM custom_exercises ORDER BY updated_at DESC, id DESC"
        ).fetchall()
    return [_custom_exercise_row(row) for row in rows if row is not None]


def get_custom_exercise(
    exercise_id: int,
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM custom_exercises WHERE id = ?",
            (int(exercise_id),),
        ).fetchone()
    return _custom_exercise_row(row)


def delete_custom_exercise(exercise_id: int, db_path: Path = DEFAULT_DB) -> None:
    with connect(db_path) as connection:
        connection.execute("DELETE FROM custom_exercises WHERE id = ?", (int(exercise_id),))


def _custom_training_plan_row(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    result = dict(row)
    result["attendance"] = _json_value(result.pop("attendance_json", ""), [])
    result["items"] = _json_value(result.pop("items_json", ""), [])
    return result


def save_custom_training_plan(
    *,
    title: str,
    training_date: str,
    attendance: list[str],
    notes: str,
    items: list[dict[str, Any]],
    plan_id: int | None = None,
    db_path: Path = DEFAULT_DB,
) -> int:
    now = datetime.now().isoformat(timespec="seconds")
    values = (
        title.strip(),
        training_date,
        json.dumps(attendance, ensure_ascii=False),
        notes.strip(),
        json.dumps(items, ensure_ascii=False),
    )
    with connect(db_path) as connection:
        if plan_id:
            connection.execute(
                """
                UPDATE custom_training_plans
                SET title = ?, training_date = ?, attendance_json = ?, notes = ?,
                    items_json = ?, updated_at = ?
                WHERE id = ?
                """,
                (*values, now, int(plan_id)),
            )
            return int(plan_id)
        cursor = connection.execute(
            """
            INSERT INTO custom_training_plans (
                title, training_date, attendance_json, notes, items_json,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (*values, now, now),
        )
        return int(cursor.lastrowid)


def list_custom_training_plans(db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT * FROM custom_training_plans ORDER BY training_date DESC, updated_at DESC"
        ).fetchall()
    return [_custom_training_plan_row(row) for row in rows if row is not None]


def get_custom_training_plan(
    plan_id: int,
    db_path: Path = DEFAULT_DB,
) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM custom_training_plans WHERE id = ?",
            (int(plan_id),),
        ).fetchone()
    return _custom_training_plan_row(row)


def delete_custom_training_plan(plan_id: int, db_path: Path = DEFAULT_DB) -> None:
    with connect(db_path) as connection:
        connection.execute("DELETE FROM custom_training_plans WHERE id = ?", (int(plan_id),))


def get_app_metadata(key: str, db_path: Path = DEFAULT_DB) -> str | None:
    with connect(db_path) as connection:
        row = connection.execute(
            "SELECT value FROM app_metadata WHERE key = ?",
            (key,),
        ).fetchone()
    return str(row["value"]) if row else None


def set_app_metadata(key: str, value: str, db_path: Path = DEFAULT_DB) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO app_metadata (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, value),
        )


def add_linked_exercise(
    title: str,
    focus: str,
    source_url: str,
    notes: str,
    db_path: Path = DEFAULT_DB,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO linked_exercises (title, focus, source_url, notes, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                title.strip(),
                focus,
                source_url.strip(),
                notes.strip(),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def list_linked_exercises(db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            "SELECT id, title, focus, source_url, notes, created_at FROM linked_exercises ORDER BY id DESC"
        ).fetchall()
    return [dict(row) for row in rows]


def save_training_log(
    *,
    plan: Any,
    goal_rating: int,
    load_rating: int,
    keep_note: str,
    change_note: str,
    db_path: Path = DEFAULT_DB,
) -> None:
    plan_data = asdict(plan)
    plan_data["training_date"] = plan.training_date.isoformat()
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO training_logs (
                training_date, primary_focus, secondary_focus, attendance,
                setter_present, hall_minutes, goal_rating, load_rating,
                keep_note, change_note, plan_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                plan.training_date.isoformat(),
                plan.primary_focus,
                plan.secondary_focus,
                plan.attendance,
                int(plan.setter_present),
                plan.hall_minutes,
                int(goal_rating),
                int(load_rating),
                keep_note.strip(),
                change_note.strip(),
                json.dumps(plan_data, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )


def save_custom_training_log(
    *,
    plan_id: int,
    plan_data: dict[str, Any],
    goal_rating: int,
    load_rating: int,
    keep_note: str,
    change_note: str,
    primary_focus: str = "custom",
    secondary_focus: str = "",
    db_path: Path = DEFAULT_DB,
) -> int:
    """Create or replace the four-part feedback for one custom training plan."""

    attendance = list(plan_data.get("attendance") or [])
    hall_minutes = sum(max(0, int(item.get("duration") or 0)) for item in plan_data.get("items", []))
    setter_names = {"Lara", "Sandra"}
    timestamp = datetime.now().isoformat(timespec="seconds")
    values = (
        str(plan_data.get("training_date") or ""),
        primary_focus,
        secondary_focus,
        len(attendance),
        int(any(name in setter_names for name in attendance)),
        hall_minutes,
        int(goal_rating),
        int(load_rating),
        keep_note.strip(),
        change_note.strip(),
        json.dumps(plan_data, ensure_ascii=False),
        timestamp,
        int(plan_id),
    )
    with connect(db_path) as connection:
        existing = connection.execute(
            "SELECT id FROM training_logs WHERE custom_plan_id = ?",
            (int(plan_id),),
        ).fetchone()
        if existing:
            connection.execute(
                """
                UPDATE training_logs
                SET training_date = ?, primary_focus = ?, secondary_focus = ?, attendance = ?,
                    setter_present = ?, hall_minutes = ?, goal_rating = ?, load_rating = ?,
                    keep_note = ?, change_note = ?, plan_json = ?, created_at = ?
                WHERE custom_plan_id = ?
                """,
                values,
            )
            return int(existing["id"])
        cursor = connection.execute(
            """
            INSERT INTO training_logs (
                training_date, primary_focus, secondary_focus, attendance,
                setter_present, hall_minutes, goal_rating, load_rating,
                keep_note, change_note, plan_json, created_at, custom_plan_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            values,
        )
        return int(cursor.lastrowid)


def list_training_logs(db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    query = """
        SELECT id, training_date, primary_focus, secondary_focus, attendance,
               setter_present, hall_minutes, goal_rating, load_rating,
               keep_note, change_note, plan_json, created_at, custom_plan_id
        FROM training_logs
        ORDER BY training_date DESC, id DESC
    """
    try:
        with connect(db_path) as connection:
            rows = connection.execute(query).fetchall()
    except sqlite3.OperationalError as error:
        # Defensive recovery for a hot Streamlit deployment whose cached
        # startup resource predates the custom_plan_id migration.
        if "custom_plan_id" not in str(error):
            raise
        init_db(db_path)
        with connect(db_path) as connection:
            rows = connection.execute(query).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        result["plan"] = _json_value(result.pop("plan_json"), {})
        results.append(result)
    return results


def delete_custom_training_log(plan_id: int, db_path: Path = DEFAULT_DB) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM training_logs WHERE custom_plan_id = ?",
            (int(plan_id),),
        )


def save_match_action(
    *,
    session_id: int = 0,
    rally_number: int = 0,
    sequence_number: int = 1,
    match_date: str,
    opponent: str,
    set_number: int,
    ball_type: str,
    receiver_id: str,
    receiver_name: str,
    first_contact_quality: str,
    first_contact_x: int | None = None,
    first_contact_y: int | None = None,
    first_contact_too_low: bool = False,
    setter_involved: bool,
    no_contact_reason: str = "",
    communication_player_ids: list[str] | tuple[str, ...] | None = None,
    communication_player_names: list[str] | tuple[str, ...] | None = None,
    setter_id: str = "",
    setter_name: str = "",
    setter_movement: str = "",
    set_quality: str = "",
    set_tendency: str = "",
    set_inside_meters: float = 0.0,
    set_origin: str = "",
    set_origin_x: float | None = None,
    set_origin_y: float | None = None,
    attacker_id: str = "",
    attacker_name: str = "",
    attack_type: str = "",
    attack_result: str = "",
    attack_block_outcome: str = "",
    attack_origin: str = "",
    landing_x: int | None = None,
    landing_y: int | None = None,
    landing_out: bool = False,
    opponent_attack_origin: str = "",
    block_player_id: str = "",
    block_player_name: str = "",
    block_result: str = "",
    block_formation: str = "",
    server_id: str = "",
    server_name: str = "",
    service_type: str = "",
    service_result: str = "",
    service_origin_x: int | None = None,
    service_origin_y: int | None = None,
    db_path: Path = DEFAULT_DB,
) -> int:
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO match_actions (
                session_id, rally_number, sequence_number,
                match_date, opponent, set_number, ball_type,
                server_id, server_name, service_type, service_result, service_origin_x, service_origin_y,
                receiver_id, receiver_name, first_contact_quality, first_contact_x, first_contact_y,
                first_contact_too_low, no_contact_reason,
                communication_player_ids_json, communication_player_names_json,
                setter_involved, setter_id, setter_name, setter_movement,
                set_quality, set_tendency, set_inside_meters, set_origin, set_origin_x, set_origin_y,
                attacker_id, attacker_name, attack_type,
                attack_result, attack_block_outcome, attack_origin,
                landing_x, landing_y, landing_out,
                opponent_attack_origin,
                block_player_id, block_player_name,
                block_result, block_formation, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(session_id),
                int(rally_number),
                int(sequence_number),
                match_date,
                opponent.strip(),
                int(set_number),
                ball_type,
                server_id,
                server_name,
                service_type,
                service_result,
                service_origin_x,
                service_origin_y,
                receiver_id,
                receiver_name,
                first_contact_quality,
                first_contact_x,
                first_contact_y,
                int(first_contact_too_low),
                no_contact_reason,
                json.dumps(list(communication_player_ids or ()), ensure_ascii=False),
                json.dumps(list(communication_player_names or ()), ensure_ascii=False),
                int(setter_involved),
                setter_id,
                setter_name,
                setter_movement,
                set_quality,
                set_tendency,
                float(set_inside_meters),
                set_origin,
                set_origin_x,
                set_origin_y,
                attacker_id,
                attacker_name,
                attack_type,
                attack_result,
                attack_block_outcome,
                attack_origin,
                landing_x,
                landing_y,
                int(landing_out),
                opponent_attack_origin,
                block_player_id,
                block_player_name,
                block_result,
                block_formation,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        return int(cursor.lastrowid)


def list_match_actions(
    db_path: Path = DEFAULT_DB,
    *,
    session_id: int | None = None,
) -> list[dict[str, Any]]:
    where = ""
    parameters: tuple[Any, ...] = ()
    if session_id is not None:
        where = "WHERE session_id = ?"
        parameters = (int(session_id),)
    with connect(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT id, session_id, rally_number, sequence_number,
                   match_date, opponent, set_number, ball_type,
                   server_id, server_name, service_type, service_result, service_origin_x, service_origin_y,
                   receiver_id, receiver_name, first_contact_quality, first_contact_x, first_contact_y,
                   first_contact_too_low, no_contact_reason,
                   communication_player_ids_json, communication_player_names_json,
                   setter_involved, setter_id, setter_name, setter_movement,
                   set_quality, set_tendency, set_inside_meters, set_origin, set_origin_x, set_origin_y,
                   attacker_id, attacker_name, attack_type,
                   attack_result, attack_block_outcome, attack_origin,
                   landing_x, landing_y, landing_out,
                   opponent_attack_origin,
                   block_player_id, block_player_name,
                   block_result, block_formation, created_at
            FROM match_actions
            {where}
            ORDER BY match_date DESC, opponent, set_number, id
            """,
            parameters,
        ).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        result = dict(row)
        result["communication_player_ids"] = _json_value(
            result.pop("communication_player_ids_json", ""),
            [],
        )
        result["communication_player_names"] = _json_value(
            result.pop("communication_player_names_json", ""),
            [],
        )
        results.append(result)
    return results


def delete_match_set_actions(
    *,
    session_id: int,
    set_number: int,
    db_path: Path = DEFAULT_DB,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM match_actions WHERE session_id = ? AND set_number = ?",
            (int(session_id), int(set_number)),
        )
        connection.execute(
            "DELETE FROM match_video_segments WHERE session_id = ? AND set_number = ?",
            (int(session_id), int(set_number)),
        )
        connection.execute(
            "DELETE FROM match_video_events WHERE session_id = ? AND set_number = ?",
            (int(session_id), int(set_number)),
        )
        connection.execute(
            """
            UPDATE match_actions
            SET set_number = set_number - 1
            WHERE session_id = ? AND set_number > ?
            """,
            (int(session_id), int(set_number)),
        )
        connection.execute(
            """
            UPDATE match_video_segments
            SET set_number = set_number - 1
            WHERE session_id = ? AND set_number > ?
            """,
            (int(session_id), int(set_number)),
        )
        connection.execute(
            """
            UPDATE match_video_events
            SET set_number = set_number - 1
            WHERE session_id = ? AND set_number > ?
            """,
            (int(session_id), int(set_number)),
        )


def delete_match_rally_actions(
    *,
    session_id: int,
    set_number: int,
    rally_number: int,
    db_path: Path = DEFAULT_DB,
) -> None:
    """Delete every recorded contact belonging to one rally."""

    with connect(db_path) as connection:
        connection.execute(
            """
            DELETE FROM match_actions
            WHERE session_id = ? AND set_number = ? AND rally_number = ?
            """,
            (int(session_id), int(set_number), int(rally_number)),
        )


def delete_match_session(
    *,
    session_id: int,
    db_path: Path = DEFAULT_DB,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM match_video_events WHERE session_id = ?",
            (int(session_id),),
        )
        connection.execute(
            "DELETE FROM match_video_segments WHERE session_id = ?",
            (int(session_id),),
        )
        connection.execute(
            "DELETE FROM match_actions WHERE session_id = ?",
            (int(session_id),),
        )
        connection.execute(
            "DELETE FROM match_sessions WHERE id = ?",
            (int(session_id),),
        )


def create_match_session(
    *,
    match_date: str,
    opponent: str,
    lineup_player_ids: list[str],
    state: dict[str, Any],
    video_url: str = "",
    video_title: str = "",
    db_path: Path = DEFAULT_DB,
) -> int:
    timestamp = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO match_sessions (
                match_date, opponent, video_url, video_title, lineup_json, state_json,
                active, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                match_date,
                opponent.strip(),
                video_url.strip(),
                video_title.strip(),
                json.dumps(lineup_player_ids),
                json.dumps(state, ensure_ascii=False),
                int(state.get("phase") != "match_over"),
                timestamp,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def _decode_match_session(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["lineup_player_ids"] = json.loads(result.pop("lineup_json"))
    result["state"] = json.loads(result.pop("state_json"))
    return result


def get_match_session(session_id: int, db_path: Path = DEFAULT_DB) -> dict[str, Any] | None:
    with connect(db_path) as connection:
        row = connection.execute(
            """
            SELECT id, match_date, opponent, video_url, video_title, lineup_json, state_json,
                   active, created_at, updated_at
            FROM match_sessions
            WHERE id = ?
            """,
            (int(session_id),),
        ).fetchone()
    return _decode_match_session(row) if row else None


def list_match_sessions(*, active_only: bool = False, db_path: Path = DEFAULT_DB) -> list[dict[str, Any]]:
    query = """
        SELECT id, match_date, opponent, video_url, video_title, lineup_json, state_json,
               active, created_at, updated_at
        FROM match_sessions
    """
    if active_only:
        query += " WHERE active = 1"
    query += " ORDER BY match_date DESC, id DESC"
    with connect(db_path) as connection:
        rows = connection.execute(query).fetchall()
    return [_decode_match_session(row) for row in rows]


def update_match_session(
    *,
    session_id: int,
    state: dict[str, Any],
    lineup_player_ids: list[str] | None = None,
    db_path: Path = DEFAULT_DB,
) -> None:
    timestamp = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as connection:
        if lineup_player_ids is None:
            connection.execute(
                """
                UPDATE match_sessions
                SET state_json = ?, active = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(state, ensure_ascii=False),
                    int(state.get("phase") != "match_over"),
                    timestamp,
                    int(session_id),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE match_sessions
                SET lineup_json = ?, state_json = ?, active = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(lineup_player_ids),
                    json.dumps(state, ensure_ascii=False),
                    int(state.get("phase") != "match_over"),
                    timestamp,
                    int(session_id),
                ),
            )


def update_match_video_url(
    *,
    session_id: int,
    video_url: str,
    video_title: str | None = None,
    db_path: Path = DEFAULT_DB,
) -> None:
    """Attach or remove the YouTube source used while coding one match."""

    with connect(db_path) as connection:
        if video_title is None:
            connection.execute(
                """
                UPDATE match_sessions
                SET video_url = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    video_url.strip(),
                    datetime.now().isoformat(timespec="seconds"),
                    int(session_id),
                ),
            )
        else:
            connection.execute(
                """
                UPDATE match_sessions
                SET video_url = ?, video_title = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    video_url.strip(),
                    video_title.strip(),
                    datetime.now().isoformat(timespec="seconds"),
                    int(session_id),
                ),
            )


def save_match_video_segment(
    *,
    session_id: int,
    set_number: int,
    rally_number: int,
    start_seconds: int,
    end_seconds: int,
    winner: str = "",
    our_score: int = 0,
    opponent_score: int = 0,
    db_path: Path = DEFAULT_DB,
) -> int:
    """Create or replace the virtual YouTube cut for one rally."""

    start_seconds = int(start_seconds)
    end_seconds = int(end_seconds)
    if start_seconds < 0:
        raise ValueError("start_seconds must not be negative")
    if end_seconds <= start_seconds:
        raise ValueError("end_seconds must be greater than start_seconds")
    timestamp = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO match_video_segments (
                session_id, set_number, rally_number,
                start_seconds, end_seconds, winner, our_score, opponent_score,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, set_number, rally_number) DO UPDATE SET
                start_seconds = excluded.start_seconds,
                end_seconds = excluded.end_seconds,
                winner = excluded.winner,
                our_score = excluded.our_score,
                opponent_score = excluded.opponent_score,
                updated_at = excluded.updated_at
            """,
            (
                int(session_id),
                int(set_number),
                int(rally_number),
                start_seconds,
                end_seconds,
                winner,
                int(our_score),
                int(opponent_score),
                timestamp,
                timestamp,
            ),
        )
        row = connection.execute(
            """
            SELECT id FROM match_video_segments
            WHERE session_id = ? AND set_number = ? AND rally_number = ?
            """,
            (int(session_id), int(set_number), int(rally_number)),
        ).fetchone()
    return int(row["id"])


def list_match_video_segments(
    *,
    session_id: int,
    db_path: Path = DEFAULT_DB,
) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, session_id, set_number, rally_number,
                   start_seconds, end_seconds, winner, our_score, opponent_score,
                   created_at, updated_at
            FROM match_video_segments
            WHERE session_id = ?
            ORDER BY set_number, rally_number
            """,
            (int(session_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_match_video_segment(
    *,
    segment_id: int,
    session_id: int,
    db_path: Path = DEFAULT_DB,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM match_video_segments WHERE id = ? AND session_id = ?",
            (int(segment_id), int(session_id)),
        )


def save_match_video_event(
    *,
    session_id: int,
    event_type: str,
    video_seconds: int,
    set_number: int,
    rally_number: int,
    our_score: int,
    opponent_score: int,
    side: str = "",
    outgoing_id: str = "",
    outgoing_name: str = "",
    incoming_id: str = "",
    incoming_name: str = "",
    db_path: Path = DEFAULT_DB,
) -> int:
    """Store a timeout or VBC substitution on the video timeline."""

    if event_type not in {"timeout", "substitution"}:
        raise ValueError("unsupported video event type")
    if side not in {"", "us", "opponent"}:
        raise ValueError("unsupported video event side")
    video_seconds = int(video_seconds)
    if video_seconds < 0:
        raise ValueError("video_seconds must not be negative")
    timestamp = datetime.now().isoformat(timespec="seconds")
    with connect(db_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO match_video_events (
                session_id, event_type, video_seconds, set_number, rally_number,
                our_score, opponent_score, side,
                outgoing_id, outgoing_name, incoming_id, incoming_name, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(session_id),
                event_type,
                video_seconds,
                int(set_number),
                int(rally_number),
                int(our_score),
                int(opponent_score),
                side,
                outgoing_id,
                outgoing_name,
                incoming_id,
                incoming_name,
                timestamp,
            ),
        )
        return int(cursor.lastrowid)


def list_match_video_events(
    *,
    session_id: int,
    db_path: Path = DEFAULT_DB,
) -> list[dict[str, Any]]:
    with connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT id, session_id, event_type, video_seconds, set_number, rally_number,
                   our_score, opponent_score, side,
                   outgoing_id, outgoing_name, incoming_id, incoming_name, created_at
            FROM match_video_events
            WHERE session_id = ?
            ORDER BY video_seconds, id
            """,
            (int(session_id),),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_match_video_event(
    *,
    event_id: int,
    session_id: int,
    db_path: Path = DEFAULT_DB,
) -> None:
    with connect(db_path) as connection:
        connection.execute(
            "DELETE FROM match_video_events WHERE id = ? AND session_id = ?",
            (int(event_id), int(session_id)),
        )
