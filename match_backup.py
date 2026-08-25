from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import re
from typing import Any, Iterable

import streamlit as st

from storage import (
    create_match_session,
    delete_match_session,
    list_match_video_events,
    list_match_video_segments,
    save_match_action,
    save_match_video_event,
    save_match_video_segment,
)


MATCH_BACKUP_FORMAT = "vbc-frauenfeld-match"
MATCH_BACKUP_VERSION = 1


def match_backup_json(
    session: dict[str, Any],
    actions: Iterable[dict[str, Any]],
    video_segments: Iterable[dict[str, Any]] = (),
    video_events: Iterable[dict[str, Any]] = (),
) -> bytes:
    payload = {
        "format": MATCH_BACKUP_FORMAT,
        "version": MATCH_BACKUP_VERSION,
        "session": {
            key: session.get(key)
            for key in (
                "match_date",
                "opponent",
                "video_url",
                "video_title",
                "lineup_player_ids",
                "state",
            )
        },
        "actions": list(actions),
        "video_segments": list(video_segments),
        "video_events": list(video_events),
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def restore_match_backup(raw_data: bytes, *, db_path: Path | None = None) -> int:
    try:
        payload = json.loads(raw_data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("Die Datei ist keine gültige Match-Sicherung.") from error
    if not isinstance(payload, dict) or payload.get("format") != MATCH_BACKUP_FORMAT:
        raise ValueError("Diese Datei ist keine Match-Sicherung von Herren-1-Matchanalyse.")
    if int(payload.get("version") or 0) != MATCH_BACKUP_VERSION:
        raise ValueError("Diese Version der Match-Sicherung wird noch nicht unterstützt.")
    session = payload.get("session")
    if not isinstance(session, dict) or not isinstance(session.get("state"), dict):
        raise ValueError("In der Sicherung fehlen die Matchdaten.")
    if not session.get("match_date") or not session.get("opponent"):
        raise ValueError("In der Sicherung fehlen Datum oder Gegner.")

    storage_kwargs = {} if db_path is None else {"db_path": db_path}
    new_session_id = create_match_session(
        match_date=str(session["match_date"]),
        opponent=str(session["opponent"]),
        lineup_player_ids=list(session.get("lineup_player_ids") or []),
        state=deepcopy(session["state"]),
        video_url=str(session.get("video_url") or ""),
        video_title=str(session.get("video_title") or ""),
        **storage_kwargs,
    )
    action_fields = {
        "rally_number",
        "sequence_number",
        "match_date",
        "opponent",
        "set_number",
        "ball_type",
        "receiver_id",
        "receiver_name",
        "first_contact_quality",
        "first_contact_x",
        "first_contact_y",
        "first_contact_too_low",
        "no_contact_reason",
        "communication_player_ids",
        "communication_player_names",
        "setter_involved",
        "setter_id",
        "setter_name",
        "setter_movement",
        "set_quality",
        "set_tendency",
        "set_inside_meters",
        "set_origin",
        "set_origin_x",
        "set_origin_y",
        "attacker_id",
        "attacker_name",
        "attack_type",
        "attack_result",
        "attack_block_outcome",
        "attack_origin",
        "landing_x",
        "landing_y",
        "landing_out",
        "opponent_attack_origin",
        "block_player_id",
        "block_player_name",
        "block_result",
        "block_formation",
        "server_id",
        "server_name",
        "service_type",
        "service_result",
        "service_origin_x",
        "service_origin_y",
    }
    try:
        for action in payload.get("actions") or []:
            if not isinstance(action, dict):
                raise ValueError("Eine Aktion in der Sicherung ist beschädigt.")
            values = {key: action[key] for key in action_fields if key in action}
            values.update(
                {
                    "session_id": new_session_id,
                    "match_date": str(values.get("match_date") or session["match_date"]),
                    "opponent": str(values.get("opponent") or session["opponent"]),
                    "receiver_id": str(values.get("receiver_id") or ""),
                    "receiver_name": str(values.get("receiver_name") or ""),
                    "first_contact_quality": str(values.get("first_contact_quality") or ""),
                    "setter_involved": bool(values.get("setter_involved")),
                }
            )
            save_match_action(**values, **storage_kwargs)
        for segment in payload.get("video_segments") or []:
            save_match_video_segment(
                session_id=new_session_id,
                set_number=int(segment["set_number"]),
                rally_number=int(segment["rally_number"]),
                start_seconds=int(segment["start_seconds"]),
                end_seconds=int(segment["end_seconds"]),
                winner=str(segment.get("winner") or ""),
                our_score=int(segment.get("our_score") or 0),
                opponent_score=int(segment.get("opponent_score") or 0),
                **storage_kwargs,
            )
        for event in payload.get("video_events") or []:
            save_match_video_event(
                session_id=new_session_id,
                event_type=str(event["event_type"]),
                video_seconds=int(event["video_seconds"]),
                set_number=int(event["set_number"]),
                rally_number=int(event["rally_number"]),
                our_score=int(event.get("our_score") or 0),
                opponent_score=int(event.get("opponent_score") or 0),
                side=str(event.get("side") or ""),
                outgoing_id=str(event.get("outgoing_id") or ""),
                outgoing_name=str(event.get("outgoing_name") or ""),
                incoming_id=str(event.get("incoming_id") or ""),
                incoming_name=str(event.get("incoming_name") or ""),
                **storage_kwargs,
            )
    except Exception:
        delete_match_session(session_id=new_session_id, **storage_kwargs)
        raise
    return new_session_id


def safe_export_name(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip()).strip("-")
    return normalized or "gegner"


def render_json_backup(
    session: dict[str, Any],
    actions: list[dict[str, Any]],
    *,
    key_suffix: str,
) -> None:
    filename_base = (
        f"match-{session.get('match_date', 'datum')}-"
        f"{safe_export_name(str(session.get('opponent') or 'gegner'))}"
    )
    st.download_button(
        "Komplette Match-Sicherung herunterladen",
        data=match_backup_json(
            session,
            actions,
            list_match_video_segments(session_id=int(session.get("id") or 0)),
            list_match_video_events(session_id=int(session.get("id") or 0)),
        ),
        file_name=f"{filename_base}.json",
        mime="application/json",
        use_container_width=True,
        key=f"match_json_backup_{session.get('id')}_{key_suffix}",
        help="Diese Datei kann später wieder vollständig in Herren-1-Matchanalyse eingelesen werden.",
    )
    st.caption(
        "Zum Weiterarbeiten mit Codex oder zum Wiederherstellen in der App. "
        "Sie enthält Matchstand, Aufstellung, alle Aktionen, Wechsel und Videomarkierungen."
    )
