"""Zentrale 5-1-Aufstellungslogik fuer VBC Frauenfeld Herren 1.

Das Modul trennt drei Dinge, die in einer Volleyball-Aufstellung leicht
vermischt werden:

* ``rotation``: die regeltechnische Position beim Service,
* ``serve_receive``: der tatsaechliche Standort fuer den ersten Ball,
* ``after_first_ball``: die feste Frauenfelder Systemposition danach.

Die Funktionen arbeiten mit stabilen Rollen-IDs.  Anzeigenamen und konkrete
Spieler-IDs werden erst in :func:`phase_snapshot` hinzugefuegt.  Dadurch
kann dieselbe Logik in Matchanalyse, Trainingsplanung und PDFs verwendet
werden.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


ROLE_SETTER = "setter"
ROLE_OPPOSITE = "opposite"
ROLE_OUTSIDE_1 = "outside_1"
ROLE_OUTSIDE_2 = "outside_2"
ROLE_MIDDLE_1 = "middle_1"
ROLE_MIDDLE_2 = "middle_2"
ROLE_LIBERO = "libero"

ROLE_ORDER = (
    ROLE_SETTER,
    ROLE_OPPOSITE,
    ROLE_OUTSIDE_1,
    ROLE_OUTSIDE_2,
    ROLE_MIDDLE_1,
    ROLE_MIDDLE_2,
    ROLE_LIBERO,
)
ROTATIONAL_ROLE_ORDER = (
    ROLE_SETTER,
    ROLE_OUTSIDE_1,
    ROLE_MIDDLE_1,
    ROLE_OPPOSITE,
    ROLE_OUTSIDE_2,
    ROLE_MIDDLE_2,
)
ROLE_LABELS = {
    ROLE_SETTER: "Zuspieler",
    ROLE_OPPOSITE: "Dia",
    ROLE_OUTSIDE_1: "Aussen 1",
    ROLE_OUTSIDE_2: "Aussen 2",
    ROLE_MIDDLE_1: "Mitte 1",
    ROLE_MIDDLE_2: "Mitte 2",
    ROLE_LIBERO: "Libero",
}

# Die Reihenfolge, in der der Zuspieler nach jedem gewonnenen Servicerecht
# weiterlaeuft.  Der Start bei L3 entspricht der vom Team gewuenschten
# Darstellung des Systembuchs.
ROTATION_ORDER = (3, 2, 1, 6, 5, 4)
RUNNER_ORDER = ROTATION_ORDER

PHASE_ROTATION = "rotation"
PHASE_SERVE_RECEIVE = "serve_receive"
PHASE_AFTER_FIRST_BALL = "after_first_ball"
SYSTEM_PHASES = (PHASE_ROTATION, PHASE_SERVE_RECEIVE, PHASE_AFTER_FIRST_BALL)

POSITION_ORDER = ("1", "2", "3", "4", "5", "6")
FRONT_POSITIONS = frozenset(("2", "3", "4"))
BACK_POSITIONS = frozenset(("1", "5", "6"))

# L1 ist die Referenz-Aufstellung.  Die weiteren Laeufer entstehen durch die
# normale Rotation 1 -> 6 -> 5 -> 4 -> 3 -> 2 -> 1.
L1_ROTATION_ROLE_POSITIONS = {
    ROLE_SETTER: "1",
    ROLE_OUTSIDE_1: "2",
    ROLE_MIDDLE_1: "3",
    ROLE_OPPOSITE: "4",
    ROLE_OUTSIDE_2: "5",
    ROLE_MIDDLE_2: "6",
}
_NEXT_POSITION = {
    "1": "6",
    "6": "5",
    "5": "4",
    "4": "3",
    "3": "2",
    "2": "1",
}

# Kompatibel mit der bisherigen Draufsicht in match_live.py.
COURT_COORDINATES = {
    "4": (70, 105),
    "3": (170, 105),
    "2": (270, 105),
    "5": (70, 265),
    "6": (170, 265),
    "1": (270, 265),
}

# Standorte fuer den ersten Ball. Die inneren Schluessel sind die
# regeltechnischen Rotationspositionen, nicht die spaeteren Systempositionen.
# Die drei Annahmespieler bilden in jeder Rotation denselben ruhigen
# P5-P6-P1-Bogen; Freilaeuferinnen bleiben mit gut lesbaren Abstaenden innerhalb
# des Feldes. So bleibt die Formation auch nach einem Spielerwechsel
# korrekt und die Ueberlappungsregeln sind weiterhin sichtbar eingehalten.
SERVE_RECEIVE_COORDINATES = {
    1: {
        "1": (295, 305),
        "2": (265, 240),
        "3": (155, 75),
        "4": (75, 145),
        "5": (75, 240),
        "6": (170, 275),
    },
    6: {
        "1": (265, 240),
        # Vordere Mitte wartet rechts am Feldrand auf Höhe der 3-m-Linie.
        "2": (301, 134),
        # Dia wartet nahe am Netz in Richtung P2, bleibt aber links von P2.
        "3": (245, 60),
        "4": (75, 240),
        "5": (170, 275),
        # Zuspieler löst sich aus P6 nach vorne in den Bereich P3.
        "6": (180, 105),
    },
    5: {
        "1": (265, 240),
        "2": (280, 90),
        "3": (75, 240),
        "4": (55, 75),
        "5": (125, 145),
        "6": (170, 275),
    },
    4: {
        "1": (295, 300),
        "2": (75, 240),
        "3": (60, 135),
        "4": (45, 70),
        "5": (170, 275),
        "6": (255, 235),
    },
    3: {
        "1": (265, 240),
        # Vordere Mitte wartet rechts am Feldrand auf Höhe der 3-m-Linie.
        "2": (301, 134),
        # Zuspieler löst sich für den ersten Ball ans Netz zwischen P3 und P2.
        "3": (195, 60),
        "4": (75, 240),
        "5": (170, 275),
        # Dia: Der Rand der 58-px-Spielermarkierung berührt die 9-m-Linie.
        "6": (225, 318),
    },
    2: {
        "1": (265, 240),
        # Zuspieler löst sich für den ersten Ball ans Netz zwischen P3 und P2.
        "2": (195, 60),
        "3": (75, 240),
        "4": (50, 75),
        # Dia: Der Rand der 58-px-Spielermarkierung berührt die 9-m-Linie.
        "5": (115, 318),
        "6": (170, 275),
    },
}


def _runner_number(runner: int | str) -> int:
    """Normalize ``1`` and ``"L1"`` to the integer runner number."""

    text = str(runner).strip().upper()
    if text.startswith("L"):
        text = text[1:]
    try:
        number = int(text)
    except ValueError as error:
        raise ValueError("runner must be L1 to L6") from error
    if number not in {1, 2, 3, 4, 5, 6}:
        raise ValueError("runner must be L1 to L6")
    return number


def next_runner(runner: int | str) -> int:
    """Return the next runner after one legal clockwise rotation."""

    number = _runner_number(runner)
    return 6 if number == 1 else number - 1


def rotation_role_positions(runner: int | str) -> dict[str, str]:
    """Return ``role -> rotation position`` for one runner.

    The setter's returned position always equals the runner number.  The
    Libero has no own rotational slot and is therefore intentionally absent.
    """

    number = _runner_number(runner)
    positions = dict(L1_ROTATION_ROLE_POSITIONS)
    while int(positions[ROLE_SETTER]) != number:
        positions = {role: _NEXT_POSITION[position] for role, position in positions.items()}
    return positions


def rotation_position_roles(runner: int | str) -> dict[str, str]:
    """Return the inverse ``rotation position -> role`` mapping."""

    return {position: role for role, position in rotation_role_positions(runner).items()}


def rotation_slots_for_lineup(
    runner: int | str,
    lineup_roles: Mapping[str, Any],
) -> dict[str, str]:
    """Return ``rotation position -> player_id`` in the established match schema.

    The Libero is not inserted here because she does not own a rotational slot.
    Match state can apply the existing Libero substitution afterwards depending
    on which team serves.
    """

    role_ids = _role_player_ids(lineup_roles)
    return {
        position: role_ids[role]
        for position, role in rotation_position_roles(runner).items()
    }


def replaced_middle_role(runner: int | str) -> str:
    """Return the back-row middle replaced by the Libero in reception."""

    positions = rotation_role_positions(runner)
    back_middles = [
        role
        for role in (ROLE_MIDDLE_1, ROLE_MIDDLE_2)
        if positions[role] in BACK_POSITIONS
    ]
    if len(back_middles) != 1:
        raise RuntimeError("a 5-1 rotation must contain exactly one back-row middle")
    return back_middles[0]


def _active_role_rotation_positions(runner: int | str) -> dict[str, str]:
    positions = rotation_role_positions(runner)
    replaced_middle = replaced_middle_role(runner)
    replaced_position = positions.pop(replaced_middle)
    positions[ROLE_LIBERO] = replaced_position
    return positions


def serve_receive_court_coordinates(rotation: int | str) -> dict[str, tuple[int, int]]:
    """Return a defensive copy of the first-ball formation for one runner.

    The result is compatible with the former helper in ``match_live.py``:
    keys are the six rotational positions and values are SVG coordinates.
    """

    number = _runner_number(rotation)
    return dict(SERVE_RECEIVE_COORDINATES[number])


def serve_receive_role_coordinates(runner: int | str) -> dict[str, tuple[int, int]]:
    """Return first-ball coordinates for the six active player roles."""

    number = _runner_number(runner)
    active_positions = _active_role_rotation_positions(number)
    coordinates = SERVE_RECEIVE_COORDINATES[number]
    return {role: coordinates[position] for role, position in active_positions.items()}


def serve_receive_lanes(runner: int | str) -> dict[str, str]:
    """Return the semantic P1/P5/P6 lanes of the three receivers.

    Frauenfeld's rules are encoded explicitly:

    * the front outside receives at P5, except at L1 where she receives at P1;
    * the Libero receives at P6 for L1/L6/L3, otherwise at P1;
    * the back outside occupies the other P1/P6 lane, except at L1 (P5).
    """

    number = _runner_number(runner)
    positions = rotation_role_positions(number)
    front_outside = next(
        role
        for role in (ROLE_OUTSIDE_1, ROLE_OUTSIDE_2)
        if positions[role] in FRONT_POSITIONS
    )
    back_outside = ROLE_OUTSIDE_2 if front_outside == ROLE_OUTSIDE_1 else ROLE_OUTSIDE_1

    if number == 1:
        return {
            front_outside: "1",
            ROLE_LIBERO: "6",
            back_outside: "5",
        }

    libero_lane = "6" if number in {3, 6} else "1"
    back_outside_lane = "1" if libero_lane == "6" else "6"
    return {
        front_outside: "5",
        ROLE_LIBERO: libero_lane,
        back_outside: back_outside_lane,
    }


def after_first_ball_role_positions(runner: int | str) -> dict[str, str]:
    """Return the fixed system position of every active role.

    Normally Pass/Dia play on P2 when front and P1 when back, Aussen on P4/P6,
    the front middle on P3, and the Libero on P5.  L1 in sideout is the sole
    team-specific exception: the front outside attacks on P2 and Dia on P4.
    """

    number = _runner_number(runner)
    rotation_positions = _active_role_rotation_positions(number)
    system_positions: dict[str, str] = {}

    for role, rotation_position in rotation_positions.items():
        is_front = rotation_position in FRONT_POSITIONS
        if role in {ROLE_SETTER, ROLE_OPPOSITE}:
            system_positions[role] = "2" if is_front else "1"
        elif role in {ROLE_OUTSIDE_1, ROLE_OUTSIDE_2}:
            system_positions[role] = "4" if is_front else "6"
        elif role in {ROLE_MIDDLE_1, ROLE_MIDDLE_2}:
            system_positions[role] = "3"
        elif role == ROLE_LIBERO:
            system_positions[role] = "5"

    if number == 1:
        front_outside = next(
            role
            for role in (ROLE_OUTSIDE_1, ROLE_OUTSIDE_2)
            if rotation_positions[role] in FRONT_POSITIONS
        )
        system_positions[front_outside] = "2"
        system_positions[ROLE_OPPOSITE] = "4"

    if set(system_positions.values()) != set(POSITION_ORDER):
        raise RuntimeError("system positions must occupy P1 to P6 exactly once")
    return system_positions


def _role_player_ids(lineup_roles: Mapping[str, Any] | None) -> dict[str, str]:
    """Normalize the established match-analysis lineup schema."""

    if lineup_roles is None:
        return {}
    outsides = list(lineup_roles.get("outsides") or ())
    middles = list(lineup_roles.get("middles") or ())
    if len(outsides) != 2 or len(middles) != 2:
        raise ValueError("lineup_roles requires exactly two outsides and two middles")
    normalized = {
        ROLE_SETTER: lineup_roles.get("setter"),
        ROLE_OPPOSITE: lineup_roles.get("opposite"),
        ROLE_OUTSIDE_1: outsides[0],
        ROLE_OUTSIDE_2: outsides[1],
        ROLE_MIDDLE_1: middles[0],
        ROLE_MIDDLE_2: middles[1],
        ROLE_LIBERO: lineup_roles.get("libero"),
    }
    if any(not player_id for player_id in normalized.values()):
        raise ValueError("lineup_roles is incomplete")
    if len(set(normalized.values())) != len(ROLE_ORDER):
        raise ValueError("every lineup role requires a different player")
    return {role: str(player_id) for role, player_id in normalized.items()}


def phase_snapshot(
    runner: int | str,
    phase: str,
    lineup_roles: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-friendly snapshot for UI, PDF and persistence layers."""

    number = _runner_number(runner)
    if phase not in SYSTEM_PHASES:
        raise ValueError(f"phase must be one of {', '.join(SYSTEM_PHASES)}")

    role_player_ids = _role_player_ids(lineup_roles)
    rotation_positions = rotation_role_positions(number)
    replaced_middle = replaced_middle_role(number)

    if phase == PHASE_ROTATION:
        active_positions = dict(rotation_positions)
        court_positions = dict(rotation_positions)
        coordinates = {
            role: COURT_COORDINATES[position]
            for role, position in active_positions.items()
        }
        lanes: dict[str, str] = {}
    elif phase == PHASE_SERVE_RECEIVE:
        active_positions = _active_role_rotation_positions(number)
        court_positions = {}
        coordinates = serve_receive_role_coordinates(number)
        lanes = serve_receive_lanes(number)
    else:
        active_positions = _active_role_rotation_positions(number)
        court_positions = after_first_ball_role_positions(number)
        coordinates = {
            role: COURT_COORDINATES[position]
            for role, position in court_positions.items()
        }
        lanes = {}

    placements = []
    for role in ROLE_ORDER:
        active = role in active_positions
        coordinate = coordinates.get(role)
        rotation_position = rotation_positions.get(role)
        replaces = None
        if role == ROLE_LIBERO and active:
            rotation_position = rotation_positions[replaced_middle]
            replaces = replaced_middle
        placements.append(
            {
                "role": role,
                "label": ROLE_LABELS[role],
                "player_id": role_player_ids.get(role),
                "rotation_position": rotation_position,
                "system_position": court_positions.get(role),
                "reception_lane": lanes.get(role),
                "x": coordinate[0] if coordinate else None,
                "y": coordinate[1] if coordinate else None,
                "active": active,
                "replaces": replaces,
            }
        )

    notes = [f"Der Zuspieler steht regeltechnisch auf P{number}; deshalb ist dies L{number}."]
    if phase in {PHASE_SERVE_RECEIVE, PHASE_AFTER_FIRST_BALL}:
        notes.append(f"Der Libero ersetzt {ROLE_LABELS[replaced_middle]} im Hinterfeld.")
    if phase == PHASE_AFTER_FIRST_BALL and number == 1:
        notes.append("L1-Sonderfall: Aussen greift auf P2 und Dia auf P4 an.")

    return {
        "runner": number,
        "runner_label": f"L{number}",
        "phase": phase,
        "placements": placements,
        "replaced_middle": replaced_middle,
        "notes": notes,
    }


def validate_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return machine-readable consistency issues for a phase snapshot."""

    issues: list[dict[str, Any]] = []
    phase = snapshot.get("phase")
    placements = list(snapshot.get("placements") or ())
    active = [placement for placement in placements if placement.get("active")]

    def add(code: str, message: str, roles: list[str] | None = None) -> None:
        issues.append(
            {
                "level": "error",
                "code": code,
                "message": message,
                "roles": roles or [],
            }
        )

    if phase not in SYSTEM_PHASES:
        add("invalid_phase", "Die Phase des Systembildes ist ungueltig.")
    if len(active) != 6:
        add("active_count", "Auf dem Feld muessen genau sechs Spieler aktiv sein.")
    active_roles = [str(placement.get("role")) for placement in active]
    if len(active_roles) != len(set(active_roles)):
        add("duplicate_role", "Eine Rolle ist auf dem Feld mehrfach vorhanden.")
    if any(placement.get("x") is None or placement.get("y") is None for placement in active):
        add("missing_coordinates", "Jede aktive Spieler braucht eine Feldkoordinate.")

    if phase == PHASE_ROTATION:
        positions = [placement.get("rotation_position") for placement in active]
        if set(positions) != set(POSITION_ORDER):
            add("rotation_positions", "Die Rotationspositionen P1 bis P6 muessen eindeutig sein.")
    elif phase == PHASE_SERVE_RECEIVE:
        receiver_roles = {ROLE_LIBERO, ROLE_OUTSIDE_1, ROLE_OUTSIDE_2}
        lanes = {
            placement.get("reception_lane")
            for placement in active
            if placement.get("role") in receiver_roles
        }
        if lanes != {"1", "5", "6"}:
            add("reception_lanes", "Die drei Annahmespieler muessen P1, P5 und P6 abdecken.")

        # At the service contact, the two players in each row must retain their
        # legal left/right order and every back-row player must remain farther
        # away from the net than the corresponding front-row player.  The SVG
        # coordinate system grows from left to right and from the net downwards.
        by_rotation_position = {
            str(placement.get("rotation_position")): placement
            for placement in active
            if placement.get("rotation_position") is not None
        }
        if set(by_rotation_position) == set(POSITION_ORDER) and not any(
            placement.get("x") is None or placement.get("y") is None
            for placement in active
        ):
            def player_text(position: str) -> str:
                placement = by_rotation_position[position]
                return str(
                    placement.get("label")
                    or ROLE_LABELS.get(str(placement.get("role")), placement.get("role"))
                    or f"P{position}"
                )

            for left_position, right_position in (("4", "3"), ("3", "2"), ("5", "6"), ("6", "1")):
                left = by_rotation_position[left_position]
                right = by_rotation_position[right_position]
                if float(left["x"]) >= float(right["x"]):
                    add(
                        f"overlap_left_right_{left_position}_{right_position}",
                        f"{player_text(left_position)} auf P{left_position} muss beim Service links von "
                        f"{player_text(right_position)} auf P{right_position} bleiben.",
                        [str(left.get("role")), str(right.get("role"))],
                    )

            for front_position, back_position in (("4", "5"), ("3", "6"), ("2", "1")):
                front = by_rotation_position[front_position]
                back = by_rotation_position[back_position]
                if float(front["y"]) >= float(back["y"]):
                    add(
                        f"overlap_front_back_{front_position}_{back_position}",
                        f"{player_text(front_position)} auf P{front_position} muss beim Service näher "
                        f"am Netz bleiben als {player_text(back_position)} auf P{back_position}.",
                        [str(front.get("role")), str(back.get("role"))],
                    )
    elif phase == PHASE_AFTER_FIRST_BALL:
        positions = [placement.get("system_position") for placement in active]
        if set(positions) != set(POSITION_ORDER):
            add("system_positions", "Die Systempositionen P1 bis P6 muessen eindeutig sein.")

    runner = snapshot.get("runner")
    setter = next(
        (placement for placement in placements if placement.get("role") == ROLE_SETTER),
        None,
    )
    if setter and str(setter.get("rotation_position")) != str(runner):
        add("setter_runner", "Die Rotationsposition der Zuspieler muss dem Laeufer entsprechen.")
    return issues


def system_court_positions(state: Mapping[str, Any]) -> dict[str, str]:
    """Return ``system position -> player_id`` for an existing match state.

    This is intentionally API-compatible with ``match_live.system_court_positions``
    so the current UI can switch to this module without changing stored matches.
    Unknown or incomplete legacy data is returned unchanged instead of crashing.
    """

    rotation_positions = dict(state.get("positions") or {})
    lineup_roles = state.get("lineup_roles") or {}
    if not rotation_positions or not lineup_roles:
        return rotation_positions

    try:
        role_ids = _role_player_ids(lineup_roles)
    except ValueError:
        return rotation_positions

    setter_dia_ids = {role_ids[ROLE_SETTER], role_ids[ROLE_OPPOSITE]}
    outside_ids = {role_ids[ROLE_OUTSIDE_1], role_ids[ROLE_OUTSIDE_2]}
    middle_libero_ids = {
        role_ids[ROLE_MIDDLE_1],
        role_ids[ROLE_MIDDLE_2],
        role_ids[ROLE_LIBERO],
    }
    system_positions: dict[str, str] = {}

    for rotation_position, player_id in rotation_positions.items():
        is_front = str(rotation_position) in FRONT_POSITIONS
        if player_id in setter_dia_ids:
            target_position = "2" if is_front else "1"
        elif player_id in outside_ids:
            target_position = "4" if is_front else "6"
        elif player_id in middle_libero_ids:
            target_position = "3" if is_front else "5"
        else:
            return rotation_positions
        if target_position in system_positions:
            return rotation_positions
        system_positions[target_position] = str(player_id)

    is_l1_sideout = (
        _runner_number(state.get("current_rotation") or 1) == 1
        and state.get("serving_team") == "opponent"
    )
    if is_l1_sideout:
        outside_front = next(
            (
                player_id
                for rotation_position, player_id in rotation_positions.items()
                if player_id in outside_ids and str(rotation_position) in FRONT_POSITIONS
            ),
            None,
        )
        opposite_id = role_ids[ROLE_OPPOSITE]
        if outside_front and opposite_id in system_positions.values():
            system_positions["2"] = str(outside_front)
            system_positions["4"] = opposite_id

    return system_positions if len(system_positions) == len(rotation_positions) else rotation_positions
