from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from copy import deepcopy
from typing import Any, Iterable

from form_rules import (
    FORM_SCALE_RULES,
    RECEPTION_FORM_METRICS,
    block_form_update,
    clamp_form_level,
    communication_form_update,
    level_after_action,
    missed_attack_threshold,
    reception_form_update,
    set_location_form_update,
    setter_movement_form_update,
    service_form_update,
)

BALL_TYPE_LABELS = {
    "service": "Service",
    "serve_receive": "Serviceannahme",
    "freeball": "Gratisball",
    "attack_defense": "Angriffsabwehr",
    "block_recycle": "Blockball zurück bei uns",
    "block": "Block",
}

NO_CONTACT_REASON_OPTIONS = ("quality", "communication")
NO_CONTACT_QUALITY_LABELS = {
    "serve_receive": "Guter Service",
    "attack_defense": "Guter Angriff",
    "freeball": "Guter Gratisball",
}


def no_contact_reason_label(ball_type: str, reason: str) -> str:
    if reason == "communication":
        return "Kommunikation"
    if reason == "quality":
        return NO_CONTACT_QUALITY_LABELS.get(ball_type, "Guter gegnerischer Ball")
    return reason


SERVICE_TYPE_LABELS = {
    "jump": "Jump",
    "standing": "Aus dem Stand",
}

SERVICE_RESULT_OPTIONS = ("ace", "very_good", "good", "okay", "error")
SERVICE_RESULT_LABELS = {
    "ace": "Ass",
    "very_good": "Sehr guter Service",
    "good": "Guter Service",
    "okay": "Okay",
    "in_play": "Okay (alte Erfassung)",
    "error": "Fehler",
}

SERVICE_LINE_COLORS = {
    "ace": "#15803d",
    "very_good": "#eab308",
    "good": "#111014",
    "okay": "#111014",
    "in_play": "#111014",
    "error": "#dc2626",
}

PERFORMANCE_METRIC_LABELS = {
    "service": "Service",
    "serve_reception": "Serviceannahme",
    "defense_reception": "Angriffs-/Gratisballabnahme",
    "setter_movement": "Zuspieler · unter dem Ball",
    "set_location": "Zuspieler · Passlage",
    "attack": "Angriff",
    "block": "Block",
}

PERFORMANCE_BENCH_COLOR = "#D4D0D6"
PERFORMANCE_LEVEL_COLORS = FORM_SCALE_RULES.level_colors

FIRST_CONTACT_LABELS = {
    "perfect": "Perfekt · 3 Angreifer nutzbar",
    "good": "Gut · 2 Angreifer nutzbar",
    "okay": "Okay · noch spielbar",
    "bad": "Schlecht · kaum oder nicht spielbar",
    "error": "Annahmefehler · direkter Punkt Gegner",
}

SETTER_MOVEMENT_LABELS = {
    "fast": "Schnell genug unter dem Ball",
    "late": "Zu spät / faul",
}

SET_QUALITY_LABELS = {
    "very_good": "Sehr gut",
    "good": "Gut",
    "okay": "Okay",
    "playable": "Spielbar",
    "bad": "Schlecht",
    # Kept for already-recorded actions from the first prototype.
    "not_good": "Pass nicht gut",
    "not_rated": "Nicht bewerten",
}

SET_TENDENCY_LABELS = {
    "optimal": "Optimal",
    "too_low": "Zu tief",
    "too_high": "Zu hoch",
    "too_far_outside": "Zu weit aussen",
    "too_far_inside": "Zu weit innen",
    "too_close_net": "Zu nahe am Netz",
    "too_far_net": "Zu weit weg vom Netz",
    "error": "Fehler",
    "not_rated": "Nicht bewerten",
}

SET_FLIGHT_DEVIATION_OPTIONS = (
    "too_low",
    "too_high",
    "too_far_outside",
    "too_far_inside",
)
SET_FLIGHT_OPTIONS = ("optimal", "error", *SET_FLIGHT_DEVIATION_OPTIONS)
SET_NET_DISTANCE_OPTIONS = ("too_close_net", "too_far_net")
SET_DEVIATION_OPTIONS = (*SET_FLIGHT_DEVIATION_OPTIONS, *SET_NET_DISTANCE_OPTIONS)

PASS_QUALITY_OPTIONS = ("very_good", "good", "okay", "playable", "bad")

ATTACK_TYPE_LABELS = {
    "spike": "Schlag",
    "safe": "Safe Ball",
    "tip": "Finte",
    "setter_tip": "Zuspielerfinte",
    "direct_return": "Ball direkt zurück",
    "second_ball_return": "2. Ball direkt rüber",
}

THIRD_BALL_ATTACK_TYPES = ("spike", "tip", "safe")

ATTACK_RESULT_LABELS = {
    "point": "Punkt",
    "continued": "Spiel geht weiter",
    "error": "Fehler",
}

ATTACK_BLOCK_OUTCOME_LABELS = {
    "none": "Kein Block / normaler Ausgang",
    "blockout": "Blockout · Punkt für uns",
    "blocked_point": "Geblockt · Punkt für Gegner",
    "recycle_us": "Blocktouch · Ball wieder bei uns",
    "touch_opponent": "Blocktouch · Ball bei Gegner",
}

BLOCK_RESULT_LABELS = {
    "no_touch": "Kein Blocktouch",
    "touch": "Blocktouch",
    "point": "Blockpunkt",
    "error": "Blockfehler",
}

BLOCK_FORMATION_LABELS = {
    "closed": "Block war zu",
    "middle_late": "Mitte zu langsam",
    "not_needed": "Kein Block nötig",
}

OPPONENT_ATTACK_ORIGIN_LABELS = {
    "outside": "Aussen",
    "middle": "Mitte",
    "opposite": "Dia",
}


def parse_set_tendencies(value: Any) -> tuple[str, ...]:
    """Return the valid technical set traits stored for one ball."""

    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = ()

    tendencies = tuple(
        dict.fromkeys(
            str(candidate).strip()
            for candidate in candidates
            if str(candidate).strip() in SET_TENDENCY_LABELS and str(candidate).strip() != "not_rated"
        )
    )
    if "error" in tendencies or any(
        tendency in SET_FLIGHT_DEVIATION_OPTIONS for tendency in tendencies
    ):
        tendencies = tuple(tendency for tendency in tendencies if tendency != "optimal")
    return tendencies


def validate_set_tendency_selection(value: Any) -> str | None:
    """Validate the independently selected flight and net-distance traits."""

    if isinstance(value, str):
        candidates = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        candidates = value
    else:
        candidates = ()
    selected = {
        str(candidate).strip()
        for candidate in candidates
        if str(candidate).strip() in SET_TENDENCY_LABELS and str(candidate).strip() != "not_rated"
    }
    flight_deviations = selected.intersection(SET_FLIGHT_DEVIATION_OPTIONS)
    flight_selected = bool(selected.intersection(SET_FLIGHT_OPTIONS))
    net_selected = selected.intersection(SET_NET_DISTANCE_OPTIONS)

    if "optimal" in selected and ("error" in selected or flight_deviations):
        return "Optimal kann nicht gleichzeitig mit Fehler oder einer Abweichung gewählt werden."
    if {"too_low", "too_high"}.issubset(selected):
        return "Bitte entweder Zu tief oder Zu hoch wählen."
    if {"too_far_inside", "too_far_outside"}.issubset(selected):
        return "Bitte entweder Zu weit innen oder Zu weit aussen wählen."
    if len(net_selected) > 1:
        return "Bitte nur eine Abweichung vom Netz wählen."
    if net_selected and not flight_selected:
        return "Bitte zusätzlich die Flugbahn bewerten: optimal, zu tief, zu hoch, zu weit innen oder zu weit aussen."
    return None


def player_can_play_role(player: Any, role: str) -> bool:
    return (
        player.primary_position == role
        or role in player.secondary_positions
        or (role == "setter" and player.backup_setter)
    )


def _validated_preferred_lineup_roles(
    players: list[Any],
    preferred_roles: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return a safe copy when a saved role assignment exactly fits the lineup."""

    if not isinstance(preferred_roles, Mapping):
        return None
    outsides = preferred_roles.get("outsides")
    middles = preferred_roles.get("middles")
    if not isinstance(outsides, (list, tuple)) or len(outsides) != 2:
        return None
    if not isinstance(middles, (list, tuple)) or len(middles) != 2:
        return None

    normalized = {
        "setter": preferred_roles.get("setter"),
        "opposite": preferred_roles.get("opposite"),
        "outsides": list(outsides),
        "middles": list(middles),
        "libero": preferred_roles.get("libero"),
    }
    role_players = (
        ("setter", normalized["setter"]),
        ("opposite", normalized["opposite"]),
        ("outside", normalized["outsides"][0]),
        ("outside", normalized["outsides"][1]),
        ("middle", normalized["middles"][0]),
        ("middle", normalized["middles"][1]),
        ("libero", normalized["libero"]),
    )
    preferred_ids = [player_id for _, player_id in role_players]
    selected_by_id = {player.id: player for player in players}
    if (
        any(not isinstance(player_id, str) or not player_id for player_id in preferred_ids)
        or len(set(preferred_ids)) != 7
        or len(selected_by_id) != 7
        or set(preferred_ids) != set(selected_by_id)
    ):
        return None
    if any(not player_can_play_role(selected_by_id[player_id], role) for role, player_id in role_players):
        return None
    return normalized


def assign_lineup_roles(
    players: Iterable[Any],
    preferred_roles: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    selected = list(players)
    if len(selected) != 7:
        return None

    preferred_assignment = _validated_preferred_lineup_roles(selected, preferred_roles)
    if preferred_assignment is not None:
        return preferred_assignment

    slots = ("setter", "libero", "middle_1", "middle_2", "opposite", "outside_1", "outside_2")

    def base_role(slot: str) -> str:
        return slot.split("_", 1)[0]

    assignment: dict[str, Any] = {}

    def fill(slot_index: int, used_ids: set[str]) -> bool:
        if slot_index == len(slots):
            return True
        slot = slots[slot_index]
        role = base_role(slot)
        for player in selected:
            if player.id in used_ids or not player_can_play_role(player, role):
                continue
            assignment[slot] = player
            if fill(slot_index + 1, used_ids | {player.id}):
                return True
            assignment.pop(slot, None)
        return False

    if not fill(0, set()):
        return None
    return {
        "setter": assignment["setter"].id,
        "opposite": assignment["opposite"].id,
        "outsides": [assignment["outside_1"].id, assignment["outside_2"].id],
        "middles": [assignment["middle_1"].id, assignment["middle_2"].id],
        "libero": assignment["libero"].id,
    }


def apply_libero_substitution(
    rotation_slots: dict[str, str],
    lineup_roles: dict[str, Any] | None,
    serving_team: str,
) -> dict[str, str]:
    positions = dict(rotation_slots)
    if not lineup_roles:
        return positions
    middle_ids = set(lineup_roles.get("middles", []))
    libero_id = lineup_roles.get("libero")
    if not libero_id:
        return positions
    for position in ("1", "6", "5"):
        player_id = rotation_slots.get(position)
        if player_id not in middle_ids:
            continue
        if position == "1" and serving_team == "us":
            continue
        positions[position] = libero_id
        break
    return positions


def setter_rotation_position(
    rotation_slots: dict[str, str],
    lineup_roles: dict[str, Any] | None,
) -> int | None:
    setter_id = (lineup_roles or {}).get("setter")
    if not setter_id:
        return None
    for position, player_id in rotation_slots.items():
        if player_id == setter_id:
            return int(position)
    return None


def lineup_role_for_player(lineup_roles: dict[str, Any] | None, player_id: str) -> str | None:
    roles = lineup_roles or {}
    for role in ("setter", "opposite", "libero"):
        if roles.get(role) == player_id:
            return role
    if player_id in roles.get("outsides", []):
        return "outside"
    if player_id in roles.get("middles", []):
        return "middle"
    return None


def substitute_match_player(
    state: dict[str, Any],
    outgoing_id: str,
    incoming_id: str,
    *,
    outgoing_name: str = "",
    incoming_name: str = "",
) -> dict[str, Any]:
    updated = deepcopy(state)
    roles = deepcopy(updated.get("lineup_roles") or {})
    role = lineup_role_for_player(roles, outgoing_id)
    if role is None:
        raise ValueError("outgoing player is not part of the lineup")
    current_ids = {
        roles.get("setter"),
        roles.get("opposite"),
        roles.get("libero"),
        *roles.get("outsides", []),
        *roles.get("middles", []),
    }
    if incoming_id in current_ids:
        raise ValueError("incoming player is already part of the lineup")

    if role in {"setter", "opposite", "libero"}:
        roles[role] = incoming_id
    else:
        collection = "outsides" if role == "outside" else "middles"
        roles[collection] = [
            incoming_id if player_id == outgoing_id else player_id for player_id in roles.get(collection, [])
        ]

    for slots_key in ("rotation_slots", "starting_rotation_slots"):
        updated[slots_key] = {
            position: incoming_id if player_id == outgoing_id else player_id
            for position, player_id in updated.get(slots_key, {}).items()
        }
    updated["lineup_roles"] = roles
    updated["positions"] = apply_libero_substitution(
        updated.get("rotation_slots", {}),
        roles,
        updated.get("serving_team", "opponent"),
    )
    updated.setdefault("substitutions", []).append(
        {
            "set_number": updated.get("current_set", 1),
            "rally_number": updated.get("rally_number", 1),
            "our_score": updated.get("our_score", 0),
            "opponent_score": updated.get("opponent_score", 0),
            "rotation": updated.get("current_rotation", 1),
            "role": role,
            "outgoing_id": outgoing_id,
            "outgoing_name": outgoing_name or outgoing_id,
            "incoming_id": incoming_id,
            "incoming_name": incoming_name or incoming_id,
        }
    )
    return updated


def new_match_state(
    first_server: str,
    *,
    starting_rotation: int = 1,
    positions: dict[str, str] | None = None,
    lineup_roles: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if first_server not in {"us", "opponent"}:
        raise ValueError("first_server must be 'us' or 'opponent'")
    rotation_slots = dict(positions or {})
    rotation = setter_rotation_position(rotation_slots, lineup_roles) or int(starting_rotation)
    return {
        "current_set": 1,
        "our_score": 0,
        "opponent_score": 0,
        "our_sets": 0,
        "opponent_sets": 0,
        "serving_team": first_server,
        "first_set_server": first_server,
        "set_start_servers": {"1": first_server},
        "phase": "opponent_turn" if first_server == "us" else "serve_receive",
        "rally_number": 1,
        "sequence_number": 1,
        "starting_rotation": rotation,
        "current_rotation": rotation,
        "rotation_slots": rotation_slots,
        "starting_rotation_slots": rotation_slots,
        "lineup_roles": dict(lineup_roles or {}),
        "positions": apply_libero_substitution(rotation_slots, lineup_roles, first_server),
        "completed_sets": [],
        "rally_history": [],
        "substitutions": [],
    }


def set_target(set_number: int) -> int:
    return 15 if set_number == 5 else 25


def is_set_won(our_score: int, opponent_score: int, set_number: int) -> bool:
    return max(our_score, opponent_score) >= set_target(set_number) and abs(our_score - opponent_score) >= 2


def new_video_cut_state() -> dict[str, Any]:
    """Scoreboard used while a coach manually separates a video into rallies."""

    return {
        "current_set": 1,
        "our_score": 0,
        "opponent_score": 0,
        "our_sets": 0,
        "opponent_sets": 0,
        "rally_number": 1,
        "phase": "cutting",
        "completed_sets": [],
        "history": [],
    }


def award_video_cut_point(state: dict[str, Any], winner: str) -> dict[str, Any]:
    """Advance the independent video-cut scoreboard by one rally."""

    if winner not in {"us", "opponent"}:
        raise ValueError("winner must be 'us' or 'opponent'")
    if state.get("phase") == "match_over":
        raise ValueError("cannot add a point after the video match has ended")
    updated = deepcopy(state)
    set_number = int(updated.get("current_set") or 1)
    rally_number = int(updated.get("rally_number") or 1)
    score_key = "our_score" if winner == "us" else "opponent_score"
    updated[score_key] = int(updated.get(score_key) or 0) + 1
    updated.setdefault("history", []).append(
        {
            "set_number": set_number,
            "rally_number": rally_number,
            "winner": winner,
            "our_score": updated["our_score"],
            "opponent_score": updated["opponent_score"],
        }
    )
    if is_set_won(updated["our_score"], updated["opponent_score"], set_number):
        set_winner = "us" if updated["our_score"] > updated["opponent_score"] else "opponent"
        updated[f"{'our' if set_winner == 'us' else 'opponent'}_sets"] += 1
        updated.setdefault("completed_sets", []).append(
            {
                "set_number": set_number,
                "our_score": updated["our_score"],
                "opponent_score": updated["opponent_score"],
                "winner": set_winner,
            }
        )
        if max(updated["our_sets"], updated["opponent_sets"]) >= 3:
            updated["phase"] = "match_over"
        else:
            updated["current_set"] = set_number + 1
            updated["our_score"] = 0
            updated["opponent_score"] = 0
            updated["rally_number"] = 1
    else:
        updated["rally_number"] = rally_number + 1
    return updated


def undo_video_cut_point(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove the latest manually cut point and rebuild its independent scoreboard."""

    history = list(state.get("history", []))
    if not history:
        raise ValueError("there is no video point to undo")
    removed = deepcopy(history[-1])
    restored = new_video_cut_state()
    for item in history[:-1]:
        restored = award_video_cut_point(restored, str(item["winner"]))
    return restored, removed


def rotate_positions(positions: dict[str, str]) -> dict[str, str]:
    if not positions:
        return {}
    return {
        "1": positions.get("2", ""),
        "2": positions.get("3", ""),
        "3": positions.get("4", ""),
        "4": positions.get("5", ""),
        "5": positions.get("6", ""),
        "6": positions.get("1", ""),
    }


def next_rotation(rotation: int) -> int:
    rotation = int(rotation)
    if rotation not in {1, 2, 3, 4, 5, 6}:
        raise ValueError("rotation must be between 1 and 6")
    return 6 if rotation == 1 else rotation - 1


def award_point(
    state: dict[str, Any],
    winner: str,
    reason: str,
    *,
    result_kind: str = "point",
) -> dict[str, Any]:
    if winner not in {"us", "opponent"}:
        raise ValueError("winner must be 'us' or 'opponent'")
    if state.get("phase") in {"set_over", "match_over"}:
        raise ValueError("cannot award a point after the set has ended")

    updated = deepcopy(state)
    score_key = "our_score" if winner == "us" else "opponent_score"
    updated[score_key] += 1
    updated["rally_history"].append(
        {
            "set_number": updated["current_set"],
            "rally_number": updated["rally_number"],
            "winner": winner,
            "reason": reason,
            "our_score": updated["our_score"],
            "opponent_score": updated["opponent_score"],
            "rotation": updated.get("current_rotation", 1),
            "result_kind": result_kind,
            "serving_team_before": updated.get("serving_team", "opponent"),
            "rotation_slots_before": deepcopy(updated.get("rotation_slots", updated.get("positions", {}))),
        }
    )

    if is_set_won(updated["our_score"], updated["opponent_score"], updated["current_set"]):
        set_winner = "us" if updated["our_score"] > updated["opponent_score"] else "opponent"
        updated[f"{'our' if set_winner == 'us' else 'opponent'}_sets"] += 1
        updated["completed_sets"].append(
            {
                "set_number": updated["current_set"],
                "our_score": updated["our_score"],
                "opponent_score": updated["opponent_score"],
                "winner": set_winner,
            }
        )
        updated["phase"] = (
            "match_over" if max(updated["our_sets"], updated["opponent_sets"]) >= 3 else "set_over"
        )
        return updated

    gains_serve = winner == "us" and updated.get("serving_team") == "opponent"
    if gains_serve:
        current_slots = updated.get("rotation_slots", updated.get("positions", {}))
        updated["rotation_slots"] = rotate_positions(current_slots)
        updated["current_rotation"] = setter_rotation_position(
            updated["rotation_slots"],
            updated.get("lineup_roles"),
        ) or next_rotation(updated.get("current_rotation", 1))

    updated["rally_number"] += 1
    updated["sequence_number"] = 1
    updated["serving_team"] = winner
    updated["phase"] = "opponent_turn" if winner == "us" else "serve_receive"
    current_slots = updated.get("rotation_slots", updated.get("positions", {}))
    updated["positions"] = apply_libero_substitution(
        current_slots,
        updated.get("lineup_roles"),
        winner,
    )
    return updated


def continue_to_opponent(state: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(state)
    updated["sequence_number"] += 1
    updated["phase"] = "opponent_turn"
    return updated


def recycle_block_to_us(state: dict[str, Any]) -> dict[str, Any]:
    """Continue the rally with our second contact after an opponent block touch."""

    updated = deepcopy(state)
    updated["sequence_number"] += 1
    updated["phase"] = "block_recycle"
    return updated


def receive_opponent_ball(state: dict[str, Any], ball_type: str) -> dict[str, Any]:
    if ball_type not in {"attack_defense", "freeball"}:
        raise ValueError("opponent ball must be an attack or freeball")
    updated = deepcopy(state)
    updated["phase"] = ball_type
    return updated


def start_block_evaluation(state: dict[str, Any]) -> dict[str, Any]:
    updated = deepcopy(state)
    updated["phase"] = "block_evaluation"
    return updated


def start_next_set(
    state: dict[str, Any],
    first_server: str,
    *,
    starting_rotation: int | None = None,
    positions: dict[str, str] | None = None,
) -> dict[str, Any]:
    if state.get("phase") != "set_over":
        raise ValueError("next set can only start after a completed set")
    if first_server not in {"us", "opponent"}:
        raise ValueError("first_server must be 'us' or 'opponent'")
    updated = deepcopy(state)
    updated["current_set"] += 1
    updated["our_score"] = 0
    updated["opponent_score"] = 0
    updated["serving_team"] = first_server
    updated.setdefault("set_start_servers", {})[str(updated["current_set"])] = first_server
    updated["phase"] = "opponent_turn" if first_server == "us" else "serve_receive"
    updated["rally_number"] = 1
    updated["sequence_number"] = 1
    next_starting_rotation = int(
        starting_rotation if starting_rotation is not None else updated.get("starting_rotation", 1)
    )
    if positions is not None:
        updated["rotation_slots"] = dict(positions)
        updated["starting_rotation_slots"] = dict(positions)
        next_starting_rotation = (
            setter_rotation_position(
                updated["rotation_slots"],
                updated.get("lineup_roles"),
            )
            or next_starting_rotation
        )
        updated["positions"] = apply_libero_substitution(
            updated["rotation_slots"],
            updated.get("lineup_roles"),
            first_server,
        )
    updated["starting_rotation"] = next_starting_rotation
    updated["current_rotation"] = next_starting_rotation
    return updated


def delete_set_from_state(
    state: dict[str, Any],
    set_number: int,
    *,
    restart_server: str = "opponent",
) -> dict[str, Any]:
    if restart_server not in {"us", "opponent"}:
        raise ValueError("restart_server must be 'us' or 'opponent'")
    updated = deepcopy(state)
    set_number = int(set_number)
    updated["completed_sets"] = [
        item for item in updated.get("completed_sets", []) if item["set_number"] != set_number
    ]
    updated["rally_history"] = [
        item for item in updated.get("rally_history", []) if item["set_number"] != set_number
    ]
    updated["substitutions"] = [
        item for item in updated.get("substitutions", []) if item["set_number"] != set_number
    ]
    for collection_name in ("completed_sets", "rally_history", "substitutions"):
        for item in updated[collection_name]:
            if item["set_number"] > set_number:
                item["set_number"] -= 1

    start_servers = updated.get("set_start_servers", {})
    updated["set_start_servers"] = {
        str(int(number) - 1 if int(number) > set_number else int(number)): server
        for number, server in start_servers.items()
        if int(number) != set_number
    }

    updated["our_sets"] = sum(1 for item in updated["completed_sets"] if item["winner"] == "us")
    updated["opponent_sets"] = sum(1 for item in updated["completed_sets"] if item["winner"] == "opponent")

    if set_number < updated["current_set"]:
        updated["current_set"] -= 1
        if updated.get("phase") == "match_over" and max(updated["our_sets"], updated["opponent_sets"]) < 3:
            updated["phase"] = "set_over"
    elif set_number == updated["current_set"]:
        updated["our_score"] = 0
        updated["opponent_score"] = 0
        updated["serving_team"] = restart_server
        updated["phase"] = "opponent_turn" if restart_server == "us" else "serve_receive"
        updated["rally_number"] = 1
        updated["sequence_number"] = 1
        updated["current_rotation"] = updated.get("starting_rotation", 1)
        updated["rotation_slots"] = dict(
            updated.get("starting_rotation_slots", updated.get("rotation_slots", {}))
        )
        updated["positions"] = apply_libero_substitution(
            updated["rotation_slots"],
            updated.get("lineup_roles"),
            restart_server,
        )

    return updated


def undo_last_point(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the state at the start of the latest rally and the removed rally record."""

    history = list(state.get("rally_history", []))
    if not history:
        raise ValueError("there is no point to undo")
    latest = deepcopy(history[-1])
    if int(latest.get("set_number") or 0) != int(state.get("current_set") or 0):
        raise ValueError("the previous set has already been left")

    updated = deepcopy(state)
    updated["rally_history"] = history[:-1]
    winner = latest.get("winner")
    updated["our_score"] = int(latest.get("our_score") or 0) - (1 if winner == "us" else 0)
    updated["opponent_score"] = int(latest.get("opponent_score") or 0) - (1 if winner == "opponent" else 0)

    removed_completed_set = next(
        (
            item
            for item in updated.get("completed_sets", [])
            if int(item.get("set_number") or 0) == int(latest["set_number"])
        ),
        None,
    )
    if removed_completed_set is not None:
        updated["completed_sets"] = [
            item
            for item in updated.get("completed_sets", [])
            if int(item.get("set_number") or 0) != int(latest["set_number"])
        ]
        set_winner = removed_completed_set.get("winner")
        if set_winner in {"us", "opponent"}:
            key = "our_sets" if set_winner == "us" else "opponent_sets"
            updated[key] = max(0, int(updated.get(key) or 0) - 1)

    serving_team_before = latest.get("serving_team_before")
    if serving_team_before not in {"us", "opponent"}:
        previous_same_set = next(
            (
                item
                for item in reversed(history[:-1])
                if int(item.get("set_number") or 0) == int(latest["set_number"])
            ),
            None,
        )
        if previous_same_set is not None:
            serving_team_before = previous_same_set.get("winner")
        else:
            serving_team_before = updated.get("set_start_servers", {}).get(str(latest["set_number"]))
    if serving_team_before not in {"us", "opponent"}:
        raise ValueError("the serving team before this point is unknown")

    rotation_slots = latest.get("rotation_slots_before")
    if not isinstance(rotation_slots, dict) or not rotation_slots:
        rotation_slots = deepcopy(updated.get("rotation_slots", {}))
        gains_serve = winner == "us" and serving_team_before == "opponent"
        if gains_serve:
            rotation_slots = {
                "1": rotation_slots.get("6", ""),
                "2": rotation_slots.get("1", ""),
                "3": rotation_slots.get("2", ""),
                "4": rotation_slots.get("3", ""),
                "5": rotation_slots.get("4", ""),
                "6": rotation_slots.get("5", ""),
            }

    updated["phase"] = "opponent_turn" if serving_team_before == "us" else "serve_receive"
    updated["serving_team"] = serving_team_before
    updated["rally_number"] = int(latest.get("rally_number") or 1)
    updated["sequence_number"] = 1
    updated["current_rotation"] = int(latest.get("rotation") or 1)
    updated["rotation_slots"] = rotation_slots
    updated["positions"] = apply_libero_substitution(
        rotation_slots,
        updated.get("lineup_roles"),
        serving_team_before,
    )
    return updated, latest


def set_quality_options(first_contact_quality: str) -> tuple[str, ...]:
    """Return the five-level setter rating scale used for every first contact."""

    return PASS_QUALITY_OPTIONS


def attack_pass_group(
    action: dict[str, Any],
    set_tendencies: tuple[str, ...],
    set_quality: str,
) -> str | None:
    """Group a rated pass by whether the attacker should normally be able to use it."""

    if not action.get("setter_involved"):
        return None
    if set_tendencies:
        usable_tendencies = {"optimal", "too_high", "too_far_inside"}
        if any(tendency not in usable_tendencies for tendency in set_tendencies):
            return "other"
        if "too_far_inside" in set_tendencies:
            try:
                inside_meters = float(action.get("set_inside_meters") or 0.0)
            except (TypeError, ValueError):
                inside_meters = 0.0
            if not 0 < inside_meters <= 1.0:
                return "other"
        return "optimal"
    if set_quality in {"very_good", "good"}:
        return "optimal"
    if set_quality in {"okay", "playable", "bad", "not_good"}:
        return "other"
    return None


def performance_levels(signals: Iterable[int], *, initial_level: int = 3) -> list[int]:
    """Start in yellow and move one of six colour levels per positive or negative event."""

    levels: list[int] = []
    current = clamp_form_level(initial_level)
    for raw_signal in signals:
        signal = max(-1, min(1, int(raw_signal)))
        current = clamp_form_level(current + signal)
        levels.append(current)
    return levels


def build_player_performance(
    actions: Iterable[dict[str, Any]],
    *,
    reset_each_set: bool = False,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Build timelines that either continue through the match or restart yellow per set."""

    raw: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))

    def add(
        player: str,
        metric: str,
        signal: int,
        action: dict[str, Any],
        detail: str,
        *,
        rating: str = "",
        service_type: str = "",
        too_low: bool = False,
        player_id: str = "",
        block_result: str = "",
        block_formation: str = "",
        source_ball_type: str = "",
    ) -> None:
        if not player:
            return
        raw[player][metric].append(
            {
                "set_number": int(action.get("set_number") or 1),
                "rally_number": int(action.get("rally_number") or 0),
                "sequence_number": int(action.get("sequence_number") or 1),
                "id": int(action.get("id") or 0),
                "signal": signal,
                "rating": rating,
                "service_type": service_type,
                "too_low": too_low,
                "player_id": player_id,
                "block_result": block_result,
                "block_formation": block_formation,
                "source_ball_type": source_ball_type,
                "detail": detail,
            }
        )

    ordered_actions = sorted(
        actions,
        key=lambda action: (
            int(action.get("set_number") or 1),
            int(action.get("rally_number") or 0),
            int(action.get("sequence_number") or 1),
            int(action.get("id") or 0),
        ),
    )
    for action in ordered_actions:
        ball_type = action.get("ball_type")
        if ball_type == "service":
            result = action.get("service_result")
            if result in SERVICE_RESULT_LABELS:
                add(
                    action.get("server_name") or "",
                    "service",
                    0,
                    action,
                    f"{SERVICE_TYPE_LABELS.get(action.get('service_type'), 'Service')} · "
                    f"{SERVICE_RESULT_LABELS[result]}",
                    rating=str(result),
                    service_type=str(action.get("service_type") or ""),
                    player_id=str(action.get("server_id") or ""),
                )
            continue

        if (
            action.get("no_contact_reason") == "communication"
            and ball_type in {"serve_receive", "attack_defense", "freeball"}
        ):
            communication_names = action.get("communication_player_names") or []
            communication_ids = action.get("communication_player_ids") or []
            reception_metric = (
                "serve_reception" if ball_type == "serve_receive" else "defense_reception"
            )
            if isinstance(communication_names, (list, tuple)):
                for index, player_name in enumerate(communication_names):
                    player_id = (
                        str(communication_ids[index])
                        if isinstance(communication_ids, (list, tuple))
                        and index < len(communication_ids)
                        else ""
                    )
                    add(
                        str(player_name),
                        reception_metric,
                        0,
                        action,
                        "Kommunikationsfehler",
                        rating="communication",
                        player_id=player_id,
                        source_ball_type=str(ball_type),
                    )

        first_quality = action.get("first_contact_quality")
        if first_quality in FIRST_CONTACT_LABELS:
            reception_metric = "serve_reception" if ball_type == "serve_receive" else "defense_reception"
            add(
                action.get("receiver_name") or "",
                reception_metric,
                0,
                action,
                FIRST_CONTACT_LABELS[first_quality].split(" · ")[0]
                + (" · zu tief" if action.get("first_contact_too_low") else ""),
                rating=first_quality,
                too_low=bool(action.get("first_contact_too_low")),
                player_id=str(action.get("receiver_id") or ""),
                source_ball_type=str(ball_type or ""),
            )

        movement = action.get("setter_movement")
        if movement in SETTER_MOVEMENT_LABELS:
            add(
                action.get("setter_name") or "",
                "setter_movement",
                0,
                action,
                SETTER_MOVEMENT_LABELS[movement],
                rating=str(movement),
                player_id=str(action.get("setter_id") or ""),
            )

        attack_type = action.get("attack_type")

        set_tendencies = parse_set_tendencies(action.get("set_tendency"))
        if action.get("setter_involved") and action.get("setter_name") and set_tendencies:
            set_rating = (
                "error"
                if "error" in set_tendencies
                else "optimal"
                if set_tendencies == ("optimal",)
                else "nonoptimal"
            )
            tendency_text = " · ".join(
                SET_TENDENCY_LABELS.get(tendency, tendency) for tendency in set_tendencies
            )
            add(
                action.get("setter_name") or "",
                "set_location",
                0,
                action,
                tendency_text,
                rating=set_rating,
                player_id=str(action.get("setter_id") or ""),
            )
        attack_result = action.get("attack_result")
        if attack_type in THIRD_BALL_ATTACK_TYPES and attack_result in ATTACK_RESULT_LABELS:
            signal = 1 if attack_result == "point" else -1 if attack_result == "error" else 0
            add(
                action.get("attacker_name") or "",
                "attack",
                signal,
                action,
                f"{ATTACK_TYPE_LABELS[attack_type]} · {ATTACK_RESULT_LABELS[attack_result]}",
                player_id=str(action.get("attacker_id") or ""),
            )

        if ball_type == "block":
            block_result = action.get("block_result")
            block_formation = action.get("block_formation")
            detail = BLOCK_RESULT_LABELS.get(block_result, "Block")
            if block_formation in BLOCK_FORMATION_LABELS:
                detail += f" · {BLOCK_FORMATION_LABELS[block_formation]}"
            add(
                action.get("block_player_name") or "",
                "block",
                0,
                action,
                detail,
                player_id=str(action.get("block_player_id") or ""),
                block_result=str(block_result or ""),
                block_formation=str(block_formation or ""),
            )

    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for player, metrics in raw.items():
        result[player] = {}
        for metric, events in metrics.items():
            levels: list[int] = []
            current_level = FORM_SCALE_RULES.initial_level
            service_good_streak = 0
            service_okay_streak = 0
            service_previous_errors = 0
            service_error_free_services = 0
            reception_too_low_count = 0
            communication_error_count = 0
            setter_fast_streak = 0
            set_location_nonoptimal_count = 0
            block_middle_late_count = 0
            block_closed_count = 0
            block_touch_count = 0
            previous_set: int | None = None
            for event in events:
                set_number = int(event["set_number"])
                if previous_set not in {None, set_number}:
                    if reset_each_set:
                        current_level = FORM_SCALE_RULES.initial_level
                    if FORM_SCALE_RULES.reset_service_streaks_each_set:
                        service_good_streak = 0
                        service_okay_streak = 0
                        service_previous_errors = 0
                        service_error_free_services = 0
                    if FORM_SCALE_RULES.reset_reception_too_low_each_set:
                        reception_too_low_count = 0
                    if FORM_SCALE_RULES.reset_communication_counter_each_set:
                        communication_error_count = 0
                    if FORM_SCALE_RULES.reset_setter_fast_streak_each_set:
                        setter_fast_streak = 0
                    if FORM_SCALE_RULES.reset_set_location_counter_each_set:
                        set_location_nonoptimal_count = 0
                    if FORM_SCALE_RULES.reset_block_counters_each_set:
                        block_middle_late_count = 0
                        block_closed_count = 0
                        block_touch_count = 0
                if metric == "service":
                    (
                        current_level,
                        service_good_streak,
                        service_okay_streak,
                        service_previous_errors,
                        service_error_free_services,
                    ) = service_form_update(
                        current_level,
                        event.get("rating") or "",
                        service_type=event.get("service_type") or "",
                        good_streak=service_good_streak,
                        okay_streak=service_okay_streak,
                        previous_errors=service_previous_errors,
                        error_free_services=service_error_free_services,
                    )
                elif metric in RECEPTION_FORM_METRICS:
                    if event.get("rating") == "communication":
                        current_level, communication_error_count = communication_form_update(
                            current_level,
                            communication_error_count,
                        )
                    else:
                        current_level, reception_too_low_count = reception_form_update(
                            current_level,
                            event.get("rating") or "",
                            service_reception=metric == "serve_reception",
                            too_low=bool(event.get("too_low")),
                            too_low_count=reception_too_low_count,
                            freeball=event.get("source_ball_type") == "freeball",
                        )
                elif metric == "setter_movement":
                    current_level, setter_fast_streak = setter_movement_form_update(
                        current_level,
                        event.get("rating") or "",
                        fast_streak=setter_fast_streak,
                    )
                elif metric == "set_location":
                    current_level, set_location_nonoptimal_count = set_location_form_update(
                        current_level,
                        optimal=event.get("rating") == "optimal",
                        error=event.get("rating") == "error",
                        nonoptimal_count=set_location_nonoptimal_count,
                    )
                elif metric == "block":
                    (
                        current_level,
                        block_middle_late_count,
                        block_closed_count,
                        block_touch_count,
                    ) = block_form_update(
                        current_level,
                        block_result=event.get("block_result") or "",
                        block_formation=event.get("block_formation") or "",
                        middle_late_count=block_middle_late_count,
                        closed_count=block_closed_count,
                        touch_count=block_touch_count,
                    )
                else:
                    current_level = level_after_action(
                        current_level,
                        metric,
                        signal=event["signal"],
                        rating=event.get("rating") or "",
                    )
                levels.append(current_level)
                previous_set = set_number
            result[player][metric] = [
                {
                    **event,
                    "level": level,
                    "color": PERFORMANCE_LEVEL_COLORS[level],
                }
                for event, level in zip(events, levels)
            ]
    return result


def _attack_inactivity_is_active(
    player_id: str,
    rally: Mapping[str, Any],
    opposite_ids: set[str],
) -> bool:
    """Return whether missed team attacks count for this player in this rally."""

    if not player_id:
        # Imported legacy matches may not contain player IDs or rotation snapshots.
        return True
    rotation_slots = rally.get("rotation_slots_before")
    if not isinstance(rotation_slots, Mapping) or not rotation_slots:
        return True
    rotation_position = next(
        (
            str(position)
            for position, positioned_id in rotation_slots.items()
            if str(positioned_id) == player_id
        ),
        "",
    )
    if not rotation_position:
        return False
    if player_id in opposite_ids:
        return True
    return rotation_position in {"2", "3", "4"}


def _player_is_on_court(
    player_id: str,
    rally: Mapping[str, Any],
    lineup_roles: Mapping[str, Any] | None,
) -> bool:
    """Return whether the player is part of the actual six for this rally."""

    if not player_id:
        return True
    rotation_slots = rally.get("rotation_slots_before")
    if not isinstance(rotation_slots, Mapping) or not rotation_slots:
        return True
    slots = {str(position): str(positioned_id) for position, positioned_id in rotation_slots.items()}
    positions = apply_libero_substitution(
        slots,
        dict(lineup_roles or {}),
        str(rally.get("serving_team_before") or "opponent"),
    )
    return player_id in {str(positioned_id) for positioned_id in positions.values()}


def _replace_lineup_role_player(
    lineup_roles: dict[str, Any],
    role: str,
    outgoing_id: str,
    incoming_id: str,
) -> None:
    if role in {"setter", "opposite", "libero"}:
        if str(lineup_roles.get(role) or "") == outgoing_id:
            lineup_roles[role] = incoming_id
        return
    if role not in {"outside", "middle"}:
        return
    collection = "outsides" if role == "outside" else "middles"
    lineup_roles[collection] = [
        incoming_id if str(player_id) == outgoing_id else player_id
        for player_id in lineup_roles.get(collection, [])
    ]


def _lineup_roles_for_rallies(
    lineup_roles: Mapping[str, Any] | None,
    substitutions: Iterable[dict[str, Any]],
    rallies: Iterable[dict[str, Any]],
) -> dict[tuple[int, int], dict[str, Any]]:
    current_roles = deepcopy(dict(lineup_roles or {}))
    substitution_list = sorted(
        (dict(substitution) for substitution in substitutions),
        key=lambda item: (
            int(item.get("set_number") or 1),
            int(item.get("rally_number") or 0),
        ),
    )
    for substitution in reversed(substitution_list):
        _replace_lineup_role_player(
            current_roles,
            str(substitution.get("role") or ""),
            str(substitution.get("incoming_id") or ""),
            str(substitution.get("outgoing_id") or ""),
        )

    snapshots: dict[tuple[int, int], dict[str, Any]] = {}
    substitution_index = 0
    for rally in rallies:
        key = (
            int(rally.get("set_number") or 1),
            int(rally.get("rally_number") or 0),
        )
        while substitution_index < len(substitution_list):
            substitution = substitution_list[substitution_index]
            substitution_key = (
                int(substitution.get("set_number") or 1),
                int(substitution.get("rally_number") or 0),
            )
            if substitution_key > key:
                break
            _replace_lineup_role_player(
                current_roles,
                str(substitution.get("role") or ""),
                str(substitution.get("outgoing_id") or ""),
                str(substitution.get("incoming_id") or ""),
            )
            substitution_index += 1
        snapshots[key] = deepcopy(current_roles)
    return snapshots


def build_player_point_performance(
    actions: Iterable[dict[str, Any]],
    rally_history: Iterable[dict[str, Any]],
    *,
    reset_each_set: bool = False,
    lineup_roles: Mapping[str, Any] | None = None,
    substitutions: Iterable[dict[str, Any]] | None = None,
    player_names_by_id: Mapping[str, str] | None = None,
) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """Expand action form to one colour value per rally.

    Reception only changes after a rated first contact. Attack inactivity is
    based on eligible team attacks while the player is on court. The missed-
    attack counter starts with the first eligible team attack.
    """

    action_list = list(actions)
    action_performance = build_player_performance(action_list)
    substitution_list = list(substitutions or [])
    opposite_ids = {
        str(player_id)
        for player_id in [
            (lineup_roles or {}).get("opposite"),
            *(
                player_id
                for substitution in substitution_list
                if substitution.get("role") == "opposite"
                for player_id in (
                    substitution.get("outgoing_id"),
                    substitution.get("incoming_id"),
                )
            ),
        ]
        if player_id
    }
    middle_ids = {
        str(player_id)
        for player_id in [
            *((lineup_roles or {}).get("middles") or []),
            *(
                player_id
                for substitution in substitution_list
                if substitution.get("role") == "middle"
                for player_id in (
                    substitution.get("outgoing_id"),
                    substitution.get("incoming_id"),
                )
            ),
        ]
        if player_id
    }
    player_ids_by_name: dict[str, str] = {}
    for player_id, player_name in (player_names_by_id or {}).items():
        if player_id and player_name:
            player_ids_by_name[str(player_name)] = str(player_id)
    for substitution in substitution_list:
        for prefix in ("outgoing", "incoming"):
            player_id = substitution.get(f"{prefix}_id")
            player_name = substitution.get(f"{prefix}_name")
            if player_id and player_name:
                player_ids_by_name[str(player_name)] = str(player_id)

    eligible_attack_ids = {
        str(player_id)
        for player_id in [
            (lineup_roles or {}).get("opposite"),
            *((lineup_roles or {}).get("outsides") or []),
            *((lineup_roles or {}).get("middles") or []),
            *(
                player_id
                for substitution in substitution_list
                if substitution.get("role") not in {"libero", "setter"}
                for player_id in (
                    substitution.get("outgoing_id"),
                    substitution.get("incoming_id"),
                )
            ),
        ]
        if player_id
    }
    setter_ids = {
        str(player_id)
        for player_id in [
            (lineup_roles or {}).get("setter"),
            *(
                player_id
                for substitution in substitution_list
                if substitution.get("role") == "setter"
                for player_id in (
                    substitution.get("outgoing_id"),
                    substitution.get("incoming_id"),
                )
            ),
        ]
        if player_id
    }
    for action in action_list:
        for prefix in ("server", "receiver", "setter", "attacker", "block_player"):
            if action.get(f"{prefix}_name") and action.get(f"{prefix}_id"):
                player_ids_by_name[str(action[f"{prefix}_name"])] = str(action[f"{prefix}_id"])
    names_by_player_id = {player_id: name for name, player_id in player_ids_by_name.items()}
    for player_id in eligible_attack_ids:
        player_name = names_by_player_id.get(player_id)
        if player_name:
            action_performance.setdefault(player_name, {}).setdefault("attack", [])
    for player_id in setter_ids:
        player_name = names_by_player_id.get(player_id)
        if player_name:
            setter_metrics = action_performance.setdefault(player_name, {})
            setter_metrics.setdefault("setter_movement", [])
            setter_metrics.setdefault("set_location", [])
    rally_by_key: dict[tuple[int, int], dict[str, Any]] = {}
    for rally in rally_history:
        key = (
            int(rally.get("set_number") or 1),
            int(rally.get("rally_number") or 0),
        )
        if key[1] > 0:
            rally_by_key[key] = dict(rally)

    # Older imported matches can contain actions without a rally-history row.
    # Keeping these synthetic rows makes their form diagram usable as well.
    for action in action_list:
        key = (
            int(action.get("set_number") or 1),
            int(action.get("rally_number") or 0),
        )
        if key[1] > 0:
            rally_by_key.setdefault(
                key,
                {
                    "set_number": key[0],
                    "rally_number": key[1],
                    "our_score": None,
                    "opponent_score": None,
                },
            )

    ordered_rallies = [rally_by_key[key] for key in sorted(rally_by_key)]
    lineup_roles_by_rally = _lineup_roles_for_rallies(
        lineup_roles,
        substitution_list,
        ordered_rallies,
    )
    team_attacks_by_rally: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for action in action_list:
        if action.get("attack_type") in THIRD_BALL_ATTACK_TYPES:
            team_attacks_by_rally[
                (
                    int(action.get("set_number") or 1),
                    int(action.get("rally_number") or 0),
                )
            ].append(action)

    result: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for player, metrics in action_performance.items():
        player_result: dict[str, list[dict[str, Any]]] = {}
        for metric, action_events in metrics.items():
            player_id = next(
                (str(event.get("player_id")) for event in action_events if event.get("player_id")),
                player_ids_by_name.get(player, ""),
            )
            if metric == "block" and middle_ids and player_id not in middle_ids:
                continue
            events_by_rally: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
            for event in action_events:
                events_by_rally[(int(event["set_number"]), int(event["rally_number"]))].append(event)

            current_level = FORM_SCALE_RULES.initial_level
            missed_team_attacks = 0
            previous_set: int | None = None
            service_good_streak = 0
            service_okay_streak = 0
            service_previous_errors = 0
            service_error_free_services = 0
            reception_too_low_count = 0
            communication_error_count = 0
            block_middle_late_count = 0
            setter_fast_streak = 0
            set_location_nonoptimal_count = 0
            block_closed_count = 0
            block_touch_count = 0
            point_events: list[dict[str, Any]] = []
            for rally in ordered_rallies:
                set_number = int(rally.get("set_number") or 1)
                rally_number = int(rally.get("rally_number") or 0)
                if previous_set is not None and set_number != previous_set:
                    if FORM_SCALE_RULES.reset_missed_team_attacks_each_set:
                        missed_team_attacks = 0
                    if FORM_SCALE_RULES.reset_service_streaks_each_set:
                        service_good_streak = 0
                        service_okay_streak = 0
                        service_previous_errors = 0
                        service_error_free_services = 0
                    if FORM_SCALE_RULES.reset_reception_too_low_each_set:
                        reception_too_low_count = 0
                    if FORM_SCALE_RULES.reset_communication_counter_each_set:
                        communication_error_count = 0
                    if FORM_SCALE_RULES.reset_block_counters_each_set:
                        block_middle_late_count = 0
                        block_closed_count = 0
                        block_touch_count = 0
                    if FORM_SCALE_RULES.reset_setter_fast_streak_each_set:
                        setter_fast_streak = 0
                    if FORM_SCALE_RULES.reset_set_location_counter_each_set:
                        set_location_nonoptimal_count = 0
                    if reset_each_set:
                        current_level = FORM_SCALE_RULES.initial_level

                action_events_here = sorted(
                    events_by_rally.get((set_number, rally_number), []),
                    key=lambda event: (
                        int(event.get("sequence_number") or 1),
                        int(event.get("id") or 0),
                    ),
                )
                degraded_for_inactivity = False
                rally_lineup_roles = lineup_roles_by_rally.get(
                    (set_number, rally_number),
                    dict(lineup_roles or {}),
                )
                on_court = _player_is_on_court(player_id, rally, rally_lineup_roles) or bool(
                    action_events_here
                )
                if action_events_here:
                    if metric == "attack":
                        missed_team_attacks = 0
                    for event in action_events_here:
                        if metric == "service":
                            (
                                current_level,
                                service_good_streak,
                                service_okay_streak,
                                service_previous_errors,
                                service_error_free_services,
                            ) = service_form_update(
                                current_level,
                                event.get("rating") or "",
                                service_type=event.get("service_type") or "",
                                good_streak=service_good_streak,
                                okay_streak=service_okay_streak,
                                previous_errors=service_previous_errors,
                                error_free_services=service_error_free_services,
                            )
                        elif metric in RECEPTION_FORM_METRICS:
                            if event.get("rating") == "communication":
                                current_level, communication_error_count = communication_form_update(
                                    current_level,
                                    communication_error_count,
                                )
                            else:
                                current_level, reception_too_low_count = reception_form_update(
                                    current_level,
                                    event.get("rating") or "",
                                    service_reception=metric == "serve_reception",
                                    too_low=bool(event.get("too_low")),
                                    too_low_count=reception_too_low_count,
                                    freeball=event.get("source_ball_type") == "freeball",
                                )
                        elif metric == "setter_movement":
                            current_level, setter_fast_streak = setter_movement_form_update(
                                current_level,
                                event.get("rating") or "",
                                fast_streak=setter_fast_streak,
                            )
                        elif metric == "set_location":
                            current_level, set_location_nonoptimal_count = set_location_form_update(
                                current_level,
                                optimal=event.get("rating") == "optimal",
                                error=event.get("rating") == "error",
                                nonoptimal_count=set_location_nonoptimal_count,
                            )
                        elif metric == "block":
                            (
                                current_level,
                                block_middle_late_count,
                                block_closed_count,
                                block_touch_count,
                            ) = block_form_update(
                                current_level,
                                block_result=event.get("block_result") or "",
                                block_formation=event.get("block_formation") or "",
                                middle_late_count=block_middle_late_count,
                                closed_count=block_closed_count,
                                touch_count=block_touch_count,
                            )
                        else:
                            current_level = level_after_action(
                                current_level,
                                metric,
                                signal=int(event.get("signal") or 0),
                                rating=event.get("rating") or "",
                            )
                    detail = " · ".join(str(event["detail"]) for event in action_events_here)
                elif metric == "attack":
                    attacks_here = team_attacks_by_rally.get((set_number, rally_number), [])
                    player_attacked_here = any(
                        action.get("attacker_name") == player for action in attacks_here
                    )
                    if player_attacked_here:
                        missed_team_attacks = 0
                        detail = "Eigener Angriff ohne Formbewertung"
                    elif not on_court:
                        detail = "Angriffszähler pausiert · nicht auf dem Feld"
                    elif not _attack_inactivity_is_active(
                        player_id,
                        rally,
                        opposite_ids,
                    ):
                        detail = "Angriffszähler pausiert · Rückraum oder nicht auf dem Feld"
                    else:
                        missed_team_attacks += len(attacks_here)
                        drops = 0
                        while missed_team_attacks >= missed_attack_threshold(current_level):
                            threshold = missed_attack_threshold(current_level)
                            current_level = clamp_form_level(current_level - 1)
                            missed_team_attacks -= threshold
                            drops += 1
                        if drops:
                            degraded_for_inactivity = True
                            detail = f"Genügend Teamangriffe ohne eigenen Angriff · Form sinkt um {drops}"
                        elif attacks_here:
                            threshold = missed_attack_threshold(current_level)
                            detail = f"{missed_team_attacks}/{threshold} Teamangriffe ohne eigenen Angriff"
                        else:
                            detail = "Kein Teamangriff in diesem Punkt"
                elif not on_court:
                    detail = "Nicht auf dem Feld"
                else:
                    detail = "Keine bewertete Aktion in diesem Punkt"

                point_events.append(
                    {
                        "set_number": set_number,
                        "rally_number": rally_number,
                        "our_score": rally.get("our_score"),
                        "opponent_score": rally.get("opponent_score"),
                        "level": current_level,
                        "color": PERFORMANCE_LEVEL_COLORS[current_level],
                        "detail": detail,
                        "had_action": bool(action_events_here),
                        "degraded_for_inactivity": degraded_for_inactivity,
                        "target_points": 15 if set_number == 5 else 25,
                        "on_court": on_court,
                    }
                )
                previous_set = set_number
            if point_events:
                player_result[metric] = point_events
        if player_result:
            result[player] = player_result
    return result


def summarize_match_actions(actions: Iterable[dict[str, Any]]) -> dict[str, Any]:
    reception_template = lambda: {
        "total": 0,
        "perfect": 0,
        "good": 0,
        "okay": 0,
        "bad": 0,
        "error": 0,
        "too_low": 0,
    }
    service_template = lambda: {
        "total": 0,
        "jump": 0,
        "standing": 0,
        "ace": 0,
        "very_good": 0,
        "good": 0,
        "okay": 0,
        "error": 0,
    }
    attack_template = lambda: {
        "total": 0,
        "optimal_pass_total": 0,
        "optimal_pass_point": 0,
        "other_pass_total": 0,
        "other_pass_point": 0,
        "spike": 0,
        "safe": 0,
        "tip": 0,
        "spike_point": 0,
        "safe_point": 0,
        "tip_point": 0,
        "point": 0,
        "error": 0,
    }
    setter_template = lambda: {
        "total": 0,
        "fast": 0,
        "late": 0,
        "very_good": 0,
        "good": 0,
        "okay": 0,
        "playable": 0,
        "bad": 0,
        "not_good": 0,
        "not_rated": 0,
        "difficult_good": 0,
        "setter_tip": 0,
        "setter_tip_point": 0,
        "optimal": 0,
        "too_low": 0,
        "too_high": 0,
        "too_far_outside": 0,
        "too_far_inside": 0,
        "too_close_net": 0,
        "too_far_net": 0,
        "error": 0,
    }
    setter_target_template = lambda: {
        "total": 0,
        "optimal": 0,
        "too_low": 0,
        "too_high": 0,
        "too_far_outside": 0,
        "too_far_inside": 0,
        "too_close_net": 0,
        "too_far_net": 0,
        "error": 0,
        "inside_meters_total": 0.0,
        "inside_meters_count": 0,
    }
    block_template = lambda: {
        "total": 0,
        "no_touch": 0,
        "touch": 0,
        "point": 0,
        "error": 0,
        "closed": 0,
        "middle_late": 0,
        "not_needed": 0,
        "opponent_origins": {},
    }
    block_origin_template = lambda: {
        "total": 0,
        "no_touch": 0,
        "touch": 0,
        "point": 0,
        "error": 0,
        "closed": 0,
        "middle_late": 0,
        "not_needed": 0,
    }

    receptions: dict[str, dict[str, int]] = defaultdict(reception_template)
    services: dict[str, dict[str, int]] = defaultdict(service_template)
    attacks: dict[str, dict[str, int]] = defaultdict(attack_template)
    setters: dict[str, dict[str, int]] = defaultdict(setter_template)
    setter_targets: dict[str, dict[str, dict[str, Any]]] = defaultdict(
        lambda: defaultdict(setter_target_template)
    )
    blocks: dict[str, dict[str, int]] = defaultdict(block_template)
    no_contact_by_ball_type: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "quality": 0, "communication": 0}
    )
    communication_groups: Counter[str] = Counter()
    no_contact_total = 0
    no_contact_quality = 0
    no_contact_communication = 0
    total_actions = 0

    for action in actions:
        total_actions += 1
        if action.get("ball_type") == "service":
            server = action.get("server_name") or "Nicht zugeordnet"
            service = services[server]
            service["total"] += 1
            service_type = action.get("service_type")
            if service_type in SERVICE_TYPE_LABELS:
                service[service_type] += 1
            service_result = action.get("service_result")
            normalized_service_result = "okay" if service_result == "in_play" else service_result
            if normalized_service_result in SERVICE_RESULT_OPTIONS:
                service[normalized_service_result] += 1
            continue
        if action.get("ball_type") == "block":
            blocker = action.get("block_player_name") or "Teamblock"
            block = blocks[blocker]
            block["total"] += 1
            block_result = action.get("block_result")
            if block_result in BLOCK_RESULT_LABELS:
                block[block_result] += 1
            block_formation = action.get("block_formation")
            if block_formation in BLOCK_FORMATION_LABELS:
                block[block_formation] += 1
            opponent_origin = action.get("opponent_attack_origin")
            if opponent_origin in OPPONENT_ATTACK_ORIGIN_LABELS:
                origin_rows = block["opponent_origins"]
                origin_row = origin_rows.setdefault(opponent_origin, block_origin_template())
                origin_row["total"] += 1
                if block_result in BLOCK_RESULT_LABELS:
                    origin_row[block_result] += 1
                if block_formation in BLOCK_FORMATION_LABELS:
                    origin_row[block_formation] += 1
            continue

        no_contact_reason = str(action.get("no_contact_reason") or "")
        if no_contact_reason in NO_CONTACT_REASON_OPTIONS:
            no_contact_total += 1
            no_contact_by_ball_type[str(action.get("ball_type") or "")]["total"] += 1
            no_contact_by_ball_type[str(action.get("ball_type") or "")][no_contact_reason] += 1
            if no_contact_reason == "quality":
                no_contact_quality += 1
            else:
                no_contact_communication += 1
                communication_names = action.get("communication_player_names") or []
                if isinstance(communication_names, (list, tuple)):
                    group_names = sorted(
                        {
                            str(name).strip()
                            for name in communication_names
                            if str(name).strip()
                        }
                    )
                    if group_names:
                        communication_groups[" / ".join(group_names)] += 1

        receiver = action.get("receiver_name") or "Nicht zugeordnet"
        first_quality = action.get("first_contact_quality")
        if first_quality in FIRST_CONTACT_LABELS:
            reception = receptions[receiver]
            reception["total"] += 1
            reception[first_quality] += 1
            if action.get("first_contact_too_low"):
                reception["too_low"] += 1

        set_quality = action.get("set_quality") or "not_rated"
        set_tendencies = parse_set_tendencies(action.get("set_tendency"))
        if action.get("setter_involved"):
            setter = action.get("setter_name") or "Nicht zugeordnet"
            setter_row = setters[setter]
            setter_row["total"] += 1
            movement = action.get("setter_movement")
            if movement in SETTER_MOVEMENT_LABELS:
                setter_row[movement] += 1
            if set_tendencies:
                for tendency in set_tendencies:
                    setter_row[tendency] += 1
            elif set_quality in SET_QUALITY_LABELS:
                setter_row[set_quality] += 1
            if set_quality == "good" and first_quality in {"okay", "bad"}:
                setter_row["difficult_good"] += 1
            if action.get("attack_type") == "setter_tip":
                setter_row["setter_tip"] += 1
                if action.get("attack_result") == "point":
                    setter_row["setter_tip_point"] += 1

            target_name = action.get("attacker_name")
            if target_name and set_tendencies:
                target_row = setter_targets[setter][target_name]
                target_row["total"] += 1
                for tendency in set_tendencies:
                    target_row[tendency] += 1
                if "too_far_inside" in set_tendencies:
                    inside_meters = float(action.get("set_inside_meters") or 0.0)
                    if inside_meters > 0:
                        target_row["inside_meters_total"] += inside_meters
                        target_row["inside_meters_count"] += 1

        attacker = action.get("attacker_name")
        attack_type = action.get("attack_type")
        if not attacker or attack_type not in THIRD_BALL_ATTACK_TYPES:
            continue
        attack = attacks[attacker]
        attack["total"] += 1
        attack[attack_type] += 1
        attack_result = action.get("attack_result")
        if attack_result in {"point", "error"}:
            attack[attack_result] += 1
        pass_group = attack_pass_group(action, set_tendencies, set_quality)
        if pass_group:
            attack[f"{pass_group}_pass_total"] += 1
            if attack_result == "point":
                attack[f"{pass_group}_pass_point"] += 1
        if attack_result == "point":
            attack[f"{attack_type}_point"] += 1

    return {
        "total_actions": total_actions,
        "total_receptions": sum(row["total"] for row in receptions.values()),
        "services": dict(services),
        "receptions": dict(receptions),
        "attacks": dict(attacks),
        "setters": dict(setters),
        "setter_targets": {setter: dict(targets) for setter, targets in setter_targets.items()},
        "blocks": dict(blocks),
        "no_contacts": {
            "total": no_contact_total,
            "quality": no_contact_quality,
            "communication": no_contact_communication,
            "by_ball_type": dict(no_contact_by_ball_type),
            "communication_groups": dict(communication_groups),
        },
    }


def summarize_phase_efficiency(
    state: dict[str, Any],
    actions: Iterable[dict[str, Any]],
    *,
    set_number: int | None = None,
) -> dict[str, Any]:
    """Summarize sideout, first-ball sideout and breakpoint by rotation and server."""

    action_list = list(actions)
    actions_by_rally: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
    for action in action_list:
        actions_by_rally[
            (
                int(action.get("set_number") or 1),
                int(action.get("rally_number") or 0),
            )
        ].append(action)

    def phase_template() -> dict[str, int]:
        return {
            "sideout_attempts": 0,
            "sideout_won": 0,
            "first_ball_sideout_attempts": 0,
            "first_ball_sideout_won": 0,
            "breakpoint_attempts": 0,
            "breakpoint_won": 0,
        }

    overall = phase_template()
    rotations: dict[int, dict[str, int]] = defaultdict(phase_template)
    servers: dict[str, dict[str, int]] = defaultdict(
        lambda: {"attempts": 0, "won": 0, "aces": 0, "errors": 0}
    )
    previous_winner_by_set: dict[int, str] = {}
    ordered_history = sorted(
        state.get("rally_history", []),
        key=lambda rally: (
            int(rally.get("set_number") or 1),
            int(rally.get("rally_number") or 0),
        ),
    )

    for rally in ordered_history:
        rally_set = int(rally.get("set_number") or 1)
        rally_number = int(rally.get("rally_number") or 0)
        rally_actions = sorted(
            actions_by_rally.get((rally_set, rally_number), []),
            key=lambda action: (
                int(action.get("sequence_number") or 1),
                int(action.get("id") or 0),
            ),
        )
        serving_team = rally.get("serving_team_before")
        if serving_team not in {"us", "opponent"}:
            serving_team = previous_winner_by_set.get(rally_set)
        if serving_team not in {"us", "opponent"}:
            if any(action.get("ball_type") == "service" for action in rally_actions):
                serving_team = "us"
            elif any(action.get("ball_type") == "serve_receive" for action in rally_actions):
                serving_team = "opponent"
            else:
                serving_team = state.get("set_start_servers", {}).get(str(rally_set))
        if serving_team not in {"us", "opponent"} and rally_set == 1:
            serving_team = state.get("first_set_server")

        previous_winner_by_set[rally_set] = str(rally.get("winner") or "")
        if set_number is not None and rally_set != int(set_number):
            continue
        if serving_team not in {"us", "opponent"}:
            continue

        rotation = int(rally.get("rotation") or 1)
        rows = (overall, rotations[rotation])
        won = rally.get("winner") == "us"
        if serving_team == "opponent":
            for row in rows:
                row["sideout_attempts"] += 1
                row["sideout_won"] += int(won)
            first_ball_actions = [
                action
                for action in rally_actions
                if action.get("ball_type") == "serve_receive" and int(action.get("sequence_number") or 1) == 1
            ]
            if first_ball_actions:
                for row in rows:
                    row["first_ball_sideout_attempts"] += 1
            first_ball_won = won and any(
                action.get("attack_result") == "point" or action.get("attack_block_outcome") == "blockout"
                for action in first_ball_actions
            )
            if first_ball_won:
                for row in rows:
                    row["first_ball_sideout_won"] += 1
        else:
            for row in rows:
                row["breakpoint_attempts"] += 1
                row["breakpoint_won"] += int(won)
            service_action = next(
                (action for action in rally_actions if action.get("ball_type") == "service"),
                None,
            )
            server_name = (service_action.get("server_name") if service_action else "") or "Nicht erfasst"
            server = servers[server_name]
            server["attempts"] += 1
            server["won"] += int(won)
            if service_action:
                server["aces"] += int(service_action.get("service_result") == "ace")
                server["errors"] += int(service_action.get("service_result") == "error")

    return {
        "overall": overall,
        "rotations": dict(rotations),
        "servers": dict(servers),
    }


def recommend_training_focuses(
    summary: dict[str, Any],
    phase_efficiency: dict[str, Any],
) -> list[dict[str, Any]]:
    """Turn sufficiently large match samples into a short, evidence-based focus list."""

    recommendations: dict[str, dict[str, Any]] = {}

    def add(focus: str, title: str, reason: str, priority: float) -> None:
        existing = recommendations.get(focus)
        if existing is None:
            recommendations[focus] = {
                "focus": focus,
                "title": title,
                "reasons": [reason],
                "priority": priority,
            }
        else:
            existing["reasons"].append(reason)
            existing["priority"] = max(float(existing["priority"]), priority)

    phase = phase_efficiency.get("overall", {})
    sideout_attempts = int(phase.get("sideout_attempts") or 0)
    sideout_won = int(phase.get("sideout_won") or 0)
    first_ball_attempts = int(phase.get("first_ball_sideout_attempts") or 0)
    first_ball_won = int(phase.get("first_ball_sideout_won") or 0)
    if sideout_attempts >= 5:
        sideout_rate = sideout_won / sideout_attempts
        first_ball_rate = first_ball_won / first_ball_attempts if first_ball_attempts else None
        if sideout_rate < 0.55:
            add(
                "sideout",
                "Sideout stabilisieren",
                f"Sideout {sideout_rate:.0%} ({sideout_won}/{sideout_attempts})",
                0.55 - sideout_rate,
            )
        if first_ball_attempts >= 5 and first_ball_rate is not None and first_ball_rate < 0.35:
            add(
                "sideout",
                "Sideout stabilisieren",
                f"First-Ball-Sideout {first_ball_rate:.0%} ({first_ball_won}/{first_ball_attempts})",
                0.35 - first_ball_rate,
            )

    breakpoint_attempts = int(phase.get("breakpoint_attempts") or 0)
    breakpoint_won = int(phase.get("breakpoint_won") or 0)
    if breakpoint_attempts >= 5:
        breakpoint_rate = breakpoint_won / breakpoint_attempts
        if breakpoint_rate < 0.35:
            add(
                "serve_pressure",
                "Mehr Druck in der Breakpointphase",
                f"Breakpoint {breakpoint_rate:.0%} ({breakpoint_won}/{breakpoint_attempts})",
                0.35 - breakpoint_rate,
            )

    total_receptions = int(summary.get("total_receptions") or 0)
    receptions = summary.get("receptions", {})
    reception_positive = sum(
        int(row.get("perfect") or 0) + int(row.get("good") or 0) for row in receptions.values()
    )
    reception_errors = sum(int(row.get("error") or 0) for row in receptions.values())
    if total_receptions >= 5:
        positive_rate = reception_positive / total_receptions
        error_rate = reception_errors / total_receptions
        if positive_rate < 0.60 or error_rate > 0.10:
            add(
                "sideout",
                "Sideout stabilisieren",
                f"Perfekte/gute erste Kontakte {positive_rate:.0%}, Fehler {error_rate:.0%}",
                max(0.60 - positive_rate, error_rate - 0.10),
            )

    attacks = summary.get("attacks", {})
    attack_total = sum(int(row.get("total") or 0) for row in attacks.values())
    attack_points = sum(int(row.get("point") or 0) for row in attacks.values())
    attack_errors = sum(int(row.get("error") or 0) for row in attacks.values())
    if attack_total >= 5:
        point_rate = attack_points / attack_total
        error_rate = attack_errors / attack_total
        if point_rate < 0.30 or error_rate > 0.20:
            add(
                "attack_variability",
                "Angriffslösungen verbessern",
                f"Angriffspunkte {point_rate:.0%}, Fehler {error_rate:.0%} ({attack_total} Aktionen)",
                max(0.30 - point_rate, error_rate - 0.20),
            )

    services = summary.get("services", {})
    service_total = sum(int(row.get("total") or 0) for row in services.values())
    service_errors = sum(int(row.get("error") or 0) for row in services.values())
    if service_total >= 5 and service_errors / service_total > 0.15:
        add(
            "serve_pressure",
            "Mehr Druck in der Breakpointphase",
            f"Servicefehler {service_errors / service_total:.0%} ({service_errors}/{service_total})",
            service_errors / service_total - 0.15,
        )

    setters = summary.get("setters", {})
    movement_total = sum(int(row.get("fast") or 0) + int(row.get("late") or 0) for row in setters.values())
    late_total = sum(int(row.get("late") or 0) for row in setters.values())
    if movement_total >= 5 and late_total / movement_total > 0.25:
        add(
            "speed",
            "Zuspielbewegung beschleunigen",
            f"Zu spät unter dem Ball {late_total / movement_total:.0%} ({late_total}/{movement_total})",
            late_total / movement_total - 0.25,
        )

    blocks = summary.get("blocks", {})
    block_total = sum(int(row.get("total") or 0) for row in blocks.values())
    block_problems = sum(
        int(row.get("error") or 0) + int(row.get("middle_late") or 0) for row in blocks.values()
    )
    if block_total >= 5 and block_problems / block_total > 0.25:
        add(
            "block_defense",
            "Block und Feldabwehr ordnen",
            f"Blockfehler/zu späte Mitte {block_problems}/{block_total}",
            block_problems / block_total - 0.25,
        )

    return sorted(
        recommendations.values(),
        key=lambda item: (-float(item["priority"]), str(item["title"])),
    )[:3]
