from __future__ import annotations

from copy import deepcopy
from datetime import date
from html import escape
import json
from pathlib import Path
import re
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

import altair as alt
import streamlit as st
import streamlit.components.v1 as components

from coach_core import default_exercises
from match_backup import (
    match_backup_json,
    render_json_backup as _render_json_backup,
    restore_match_backup,
    safe_export_name as _safe_export_name,
)
from match_pdf_export import player_analysis_pdf
from match_analysis import (
    ATTACK_BLOCK_OUTCOME_LABELS,
    ATTACK_RESULT_LABELS,
    ATTACK_TYPE_LABELS,
    BALL_TYPE_LABELS,
    BLOCK_FORMATION_LABELS,
    NO_CONTACT_REASON_OPTIONS,
    BLOCK_RESULT_LABELS,
    FIRST_CONTACT_LABELS,
    PASS_QUALITY_OPTIONS,
    SET_FLIGHT_OPTIONS,
    SET_NET_DISTANCE_OPTIONS,
    SET_QUALITY_LABELS,
    SET_TENDENCY_LABELS,
    SETTER_MOVEMENT_LABELS,
    PERFORMANCE_BENCH_COLOR,
    SERVICE_RESULT_LABELS,
    SERVICE_RESULT_OPTIONS,
    SERVICE_LINE_COLORS,
    SERVICE_TYPE_LABELS,
    THIRD_BALL_ATTACK_TYPES,
    PERFORMANCE_LEVEL_COLORS,
    PERFORMANCE_METRIC_LABELS,
    OPPONENT_ATTACK_ORIGIN_LABELS,
    apply_libero_substitution,
    assign_lineup_roles,
    award_video_cut_point,
    award_point,
    build_player_point_performance,
    continue_to_opponent,
    delete_set_from_state,
    lineup_role_for_player,
    new_match_state,
    new_video_cut_state,
    no_contact_reason_label,
    parse_set_tendencies,
    player_can_play_role,
    receive_opponent_ball,
    recommend_training_focuses,
    recycle_block_to_us,
    rotate_positions,
    set_quality_options,
    set_target,
    setter_rotation_position,
    start_block_evaluation,
    start_next_set,
    substitute_match_player,
    summarize_match_actions,
    summarize_phase_efficiency,
    undo_last_point,
    undo_video_cut_point,
    validate_set_tendency_selection,
)
from storage import (
    create_match_session,
    delete_match_session,
    delete_match_rally_actions,
    delete_match_set_actions,
    delete_match_video_event,
    delete_match_video_segment,
    get_app_metadata,
    get_match_session,
    list_match_actions,
    list_match_sessions,
    list_match_video_events,
    list_match_video_segments,
    save_match_action,
    save_match_video_event,
    save_match_video_segment,
    set_app_metadata,
    update_match_session,
    update_match_video_url,
)
from system_book import (
    COURT_COORDINATES,
    SERVE_RECEIVE_COORDINATES,
    rotation_slots_for_lineup,
    serve_receive_court_coordinates,
    system_court_positions,
)
from volleyball_net import (
    WOMENS_NET_BOTTOM_METERS,
    WOMENS_NET_TOP_METERS,
    net_mesh_heights,
    net_mesh_positions,
)


TEAM_NAME = "VBC Frauenfeld Herren 1"
PLAYER_ANALYSIS_PDF_VERSION = 16
SYSTEM_BOOK_LINEUP_METADATA_KEY = "system_book_lineup_v1"

YOUTUBE_CUTTER = components.declare_component(
    "youtube_rally_cutter",
    path=str(Path(__file__).resolve().parent),
)

MATCH_ROLE_LABELS = {
    "setter": "Zuspieler",
    "opposite": "Dia",
    "outside": "Aussen",
    "middle": "Mitte",
    "libero": "Libero",
}

TRAINING_EXERCISE_IDS = {
    "sideout": ("sideout_three_balls", "sideout_rotation_ladder"),
    "serve_pressure": ("sv_zone_serve", "serve_pressure_finish"),
    "attack_variability": ("attack_toolbox", "k1_wash_two_solutions"),
    "speed": ("transition_first_three_steps", "setter_choice_three_front"),
    "block_defense": ("middle_block_transition", "vc_team_block"),
}


def rate_with_counts(successes: int, attempts: int) -> str:
    return f"{successes / attempts:.0%} ({successes} / {attempts})" if attempts else "– (0 / 0)"


YOUTUBE_VIDEO_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{6,20}$")


def normalize_youtube_url(value: str) -> str:
    """Return a stable watch URL for common YouTube link formats."""

    raw = value.strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    video_id = ""
    if host == "youtu.be":
        video_id = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "m.youtube.com", "music.youtube.com"}:
        if parsed.path.rstrip("/") == "/watch":
            video_id = parse_qs(parsed.query).get("v", [""])[0]
        else:
            parts = [part for part in parsed.path.split("/") if part]
            if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live"}:
                video_id = parts[1]
    if not YOUTUBE_VIDEO_ID_PATTERN.fullmatch(video_id):
        raise ValueError("Bitte einen gültigen YouTube-Link einfügen.")
    return f"https://www.youtube.com/watch?v={video_id}"


def parse_video_timestamp(value: str) -> int:
    """Parse S, MM:SS, or HH:MM:SS into whole seconds."""

    text = value.strip()
    if not text:
        raise ValueError("Die Zeit darf nicht leer sein.")
    parts = text.split(":")
    if len(parts) > 3 or any(not part.isdigit() for part in parts):
        raise ValueError("Zeit bitte als MM:SS eingeben, zum Beispiel 12:34.")
    numbers = [int(part) for part in parts]
    if len(numbers) >= 2 and any(number >= 60 for number in numbers[1:]):
        raise ValueError("Minuten und Sekunden hinter einem Doppelpunkt müssen unter 60 sein.")
    if len(numbers) == 1:
        return numbers[0]
    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]


def format_video_timestamp(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, remainder = divmod(seconds, 3600)
    minutes, remaining_seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{remaining_seconds:02d}"
    return f"{minutes:02d}:{remaining_seconds:02d}"


def _players_by_id(roster: Iterable[Any]) -> dict[str, Any]:
    return {player.id: player for player in roster}


PLAYER_COLORS = {
    "setter": "#7561a8",
    "middle": "#18141d",
    "outside": "#F6B4CD",
    "opposite": "#5c467e",
    "libero": "#8f5b6e",
}


def eligible_blocker_ids(state: dict[str, Any], opponent_attack_origin: str) -> list[str]:
    """Return only the front players who can physically block this attack lane."""

    target_positions = {
        "outside": ("2", "3"),
        "middle": ("2", "3", "4"),
        "opposite": ("4", "3"),
    }
    if opponent_attack_origin not in target_positions:
        return []
    system_positions = system_court_positions(state)
    return list(
        dict.fromkeys(
            player_id
            for position in target_positions[opponent_attack_origin]
            if (player_id := system_positions.get(position))
        )
    )


def attack_type_options_for_player(
    player_id: str,
    lineup_roles: dict[str, Any] | None,
) -> tuple[str, ...]:
    """The acting Libero may only send the third ball over as a safe ball."""

    if player_id and player_id == (lineup_roles or {}).get("libero"):
        return ("safe",)
    return ("spike", "safe", "tip")


ATTACK_ORIGIN_LABELS = {
    "4": "P4 · Aussen vorne",
    "3": "P3 · Mitte vorne",
    "2": "P2 · Dia/Zuspieler vorne",
    "6": "P6 · Aussen aus dem Rückraum hinter der 3-m-Linie",
    "1": "P1 · Dia/Zuspieler aus dem Rückraum hinter der 3-m-Linie",
    "5": "P5 · Hinterfeld",
}

ATTACK_ORIGIN_COORDINATES = {
    "4": (1.0, -0.8),
    "3": (4.5, -0.8),
    "2": (8.0, -0.8),
    "6": (4.5, -3.25),
    "1": (8.0, -3.25),
    "5": (1.0, -5.5),
}

ATTACK_LINE_COLORS = {
    "point": "#15803d",
    "continued": "#111014",
    "error": "#dc2626",
}

SET_TRAIT_COLORS = {
    "error": "#dc2626",
    "optimal": "#15803d",
    "too_low": "#2563eb",
    "too_high": "#f97316",
    "too_far_outside": "#eab308",
    "too_far_inside": "#0891b2",
}

DEFAULT_SET_ORIGIN = (7.4, 1.1)

# x = Meter von links, y = Meter ab Netz, z = Angriffskontakt über Boden.
PASS_ATTACK_TARGETS_3D = {
    "4": (0.7, 0.45, 2.55),
    "3": (4.5, 0.35, 2.65),
    "2": (8.3, 0.45, 2.55),
    "5": (0.9, 5.8, 2.15),
    "6": (4.5, 3.0, 2.55),
    "1": (8.1, 3.0, 2.55),
}


def attack_origin_for_player(state: dict[str, Any], player_id: str) -> str:
    """Return the player's system position at the moment of attack."""
    for position, positioned_player_id in system_court_positions(state).items():
        if positioned_player_id == player_id:
            return position
    return ""


def landing_cell_is_out(cell_x: int, cell_y: int) -> bool:
    return not (0 <= int(cell_x) <= 8 and 0 <= int(cell_y) <= 8)


def landing_cell_label(cell_x: int, cell_y: int) -> str:
    if landing_cell_is_out(cell_x, cell_y):
        directions: list[str] = []
        if cell_x < 0:
            directions.append("links")
        elif cell_x > 8:
            directions.append("rechts")
        if cell_y < 0:
            directions.append("kurz am Netz")
        elif cell_y > 8:
            directions.append("hinter der Grundlinie")
        return "OUT · " + " / ".join(directions)
    return f"Feld · Meter {cell_x + 1} von links · Meter {cell_y + 1} ab Netz"


def landing_grid_chart(selected_cell: tuple[int, int] | None = None) -> alt.Chart:
    cells = []
    for cell_y in range(-1, 10):
        for cell_x in range(-1, 10):
            is_selected = selected_cell == (cell_x, cell_y)
            cells.append(
                {
                    "cell_x": cell_x,
                    "cell_y": cell_y,
                    "x0": cell_x,
                    "x1": cell_x + 1,
                    "y0": cell_y,
                    "y1": cell_y + 1,
                    "fill": "#7561a8"
                    if is_selected
                    else ("#F6B4CD" if not landing_cell_is_out(cell_x, cell_y) else "#e5e1e6"),
                    "border": "#111014" if is_selected else "#ffffff",
                    "border_width": 2.4 if is_selected else 0.65,
                    "zone": landing_cell_label(cell_x, cell_y),
                }
            )
    selection = alt.selection_point(
        name="landing_cell",
        fields=["cell_x", "cell_y"],
        on="click",
        toggle=False,
        clear=False,
    )
    grid = (
        alt.Chart(alt.Data(values=cells))
        .mark_rect()
        .encode(
            x=alt.X(
                "x0:Q",
                scale=alt.Scale(domain=[-1, 10], nice=False),
                axis=alt.Axis(title="Breite · Meter", values=list(range(-1, 11)), grid=False),
            ),
            x2="x1:Q",
            y=alt.Y(
                "y0:Q",
                scale=alt.Scale(domain=[-1, 10], nice=False),
                axis=alt.Axis(title="Entfernung ab Netz · Meter", values=list(range(-1, 11)), grid=False),
            ),
            y2="y1:Q",
            color=alt.condition(
                selection,
                alt.value("#7561a8"),
                alt.Color("fill:N", scale=None, legend=None),
            ),
            stroke=alt.condition(
                selection,
                alt.value("#111014"),
                alt.Stroke("border:N", scale=None, legend=None),
            ),
            strokeWidth=alt.condition(
                selection,
                alt.value(2.4),
                alt.StrokeWidth("border_width:Q", legend=None),
            ),
            tooltip=[alt.Tooltip("zone:N", title="Ziel")],
        )
        .add_params(selection)
    )
    court_boundary = (
        alt.Chart(alt.Data(values=[{"x0": 0, "x1": 9, "y0": 0, "y1": 9}]))
        .mark_rect(fillOpacity=0, stroke="#ffffff", strokeWidth=2.2)
        .encode(x="x0:Q", x2="x1:Q", y="y0:Q", y2="y1:Q")
    )
    attack_line = (
        alt.Chart(alt.Data(values=[{"x0": 0, "x1": 9, "line": 3}]))
        .mark_rule(color="#ffffff", strokeWidth=2.2)
        .encode(x="x0:Q", x2="x1:Q", y="line:Q")
    )
    net = alt.Chart(alt.Data(values=[{"net": 0}])).mark_rule(color="#09090b", strokeWidth=6).encode(y="net:Q")
    return (grid + court_boundary + attack_line + net).properties(width=500, height=500)


def service_origin_label(cell_x: int) -> str:
    if not 0 <= int(cell_x) < 9:
        raise ValueError("service origin must be inside the 9-metre service zone")
    return f"Serviceort · {int(cell_x) + 1}. Meter von links hinter der Grundlinie"


def service_origin_chart(selected_cell: int | None = None) -> alt.Chart:
    cells = [
        {
            "cell_x": cell_x,
            "x0": cell_x,
            "x1": cell_x + 1,
            "y0": 0,
            "y1": 1,
            "fill": "#7561a8" if selected_cell == cell_x else "#F6B4CD",
            "border": "#111014" if selected_cell == cell_x else "#ffffff",
            "border_width": 2.4 if selected_cell == cell_x else 0.65,
            "zone": service_origin_label(cell_x),
        }
        for cell_x in range(9)
    ]
    selection = alt.selection_point(
        name="service_origin_cell",
        fields=["cell_x"],
        on="click",
        toggle=False,
        clear=False,
    )
    grid = (
        alt.Chart(alt.Data(values=cells))
        .mark_rect()
        .encode(
            x=alt.X(
                "x0:Q",
                scale=alt.Scale(domain=[0, 9], nice=False),
                axis=alt.Axis(
                    title="Breite hinter der Grundlinie · Meter",
                    values=list(range(10)),
                    grid=False,
                ),
            ),
            x2="x1:Q",
            y=alt.Y(
                "y0:Q",
                scale=alt.Scale(domain=[1, 0], nice=False),
                axis=None,
            ),
            y2="y1:Q",
            color=alt.condition(
                selection,
                alt.value("#7561a8"),
                alt.Color("fill:N", scale=None, legend=None),
            ),
            stroke=alt.condition(
                selection,
                alt.value("#111014"),
                alt.Stroke("border:N", scale=None, legend=None),
            ),
            strokeWidth=alt.condition(
                selection,
                alt.value(2.4),
                alt.StrokeWidth("border_width:Q", legend=None),
            ),
            tooltip=[alt.Tooltip("zone:N", title="Serviceort")],
        )
        .add_params(selection)
    )
    baseline = (
        alt.Chart(alt.Data(values=[{"line": 0}]))
        .mark_rule(
            color="#09090b",
            strokeWidth=5,
        )
        .encode(y="line:Q")
    )
    return (grid + baseline).properties(width=450, height=50)


def service_placement_chart(
    selected_target: tuple[int, int] | None = None,
    selected_origin: int | None = None,
) -> alt.Chart:
    """Show service target and the nine one-metre service-start cells together."""
    cells = []
    for cell_y in range(-1, 10):
        for cell_x in range(-1, 10):
            is_selected = selected_target == (cell_x, cell_y)
            cells.append(
                {
                    "kind": "target",
                    "cell_x": cell_x,
                    "cell_y": cell_y,
                    "x0": cell_x,
                    "x1": cell_x + 1,
                    "y0": cell_y,
                    "y1": cell_y + 1,
                    "fill": "#7561a8"
                    if is_selected
                    else ("#F6B4CD" if not landing_cell_is_out(cell_x, cell_y) else "#e5e1e6"),
                    "border": "#111014" if is_selected else "#ffffff",
                    "border_width": 2.4 if is_selected else 0.65,
                    "zone": landing_cell_label(cell_x, cell_y),
                }
            )
    for cell_x in range(9):
        is_selected = selected_origin == cell_x
        cells.append(
            {
                "kind": "origin",
                "cell_x": cell_x,
                "cell_y": -2,
                "x0": cell_x,
                "x1": cell_x + 1,
                "y0": -2,
                "y1": -1,
                "fill": "#7561a8" if is_selected else "#F6B4CD",
                "border": "#111014" if is_selected else "#ffffff",
                "border_width": 2.4 if is_selected else 1.2,
                "zone": service_origin_label(cell_x),
            }
        )
    selection = alt.selection_point(
        name="service_cell",
        fields=["kind", "cell_x", "cell_y"],
        on="click",
        toggle=False,
        clear=False,
    )
    grid = (
        alt.Chart(alt.Data(values=cells))
        .mark_rect()
        .encode(
            x=alt.X(
                "x0:Q",
                scale=alt.Scale(domain=[-1, 10], nice=False),
                axis=alt.Axis(title="Breite · Meter", values=list(range(-1, 11)), grid=False),
            ),
            x2="x1:Q",
            y=alt.Y(
                "y0:Q",
                scale=alt.Scale(domain=[-2, 10], nice=False),
                axis=alt.Axis(
                    title="Entfernung ab Netz · Meter",
                    values=[-1, 1, 3, 5, 7, 9],
                    grid=False,
                ),
            ),
            y2="y1:Q",
            color=alt.Color("fill:N", scale=None, legend=None),
            stroke=alt.Stroke("border:N", scale=None, legend=None),
            strokeWidth=alt.StrokeWidth("border_width:Q", legend=None),
            tooltip=[alt.Tooltip("zone:N", title="Auswahl")],
        )
        .add_params(selection)
    )
    court_boundary = (
        alt.Chart(alt.Data(values=[{"x0": 0, "x1": 9, "y0": 0, "y1": 9}]))
        .mark_rect(fillOpacity=0, stroke="#ffffff", strokeWidth=2.2)
        .encode(x="x0:Q", x2="x1:Q", y="y0:Q", y2="y1:Q")
    )
    attack_line = (
        alt.Chart(alt.Data(values=[{"x0": 0, "x1": 9, "line": 3}]))
        .mark_rule(color="#ffffff", strokeWidth=2.2)
        .encode(x="x0:Q", x2="x1:Q", y="line:Q")
    )
    net = alt.Chart(alt.Data(values=[{"net": 0}])).mark_rule(color="#09090b", strokeWidth=6).encode(y="net:Q")
    return (grid + court_boundary + attack_line + net).properties(width=500, height=545)


def service_trajectory_svg(
    player_name: str,
    actions: Iterable[dict[str, Any]],
) -> str:
    action_list = [
        action
        for action in actions
        if action.get("service_origin_x") is not None
        and action.get("landing_x") is not None
        and action.get("landing_y") is not None
        and 0 <= int(action["service_origin_x"]) <= 8
        and action.get("service_result") in SERVICE_LINE_COLORS
    ]

    def px_x(value: float) -> float:
        return 25 + (value + 1) * 23

    def px_y(value: float) -> float:
        return 280 - value * 23

    grid_parts = [
        '<rect x="25" y="50" width="253" height="253" fill="#e5e1e6" rx="4" />',
        f'<rect x="{px_x(0)}" y="{px_y(9)}" width="{9 * 23}" height="{9 * 23}" fill="#F6B4CD" stroke="#ffffff" stroke-width="2.2" />',
        f'<rect x="{px_x(0)}" y="{px_y(0)}" width="{9 * 23}" height="{9 * 23}" fill="#fff0f6" stroke="#d8d1dd" stroke-width="2.2" />',
        f'<line x1="{px_x(0)}" y1="{px_y(3)}" x2="{px_x(9)}" y2="{px_y(3)}" stroke="#ffffff" stroke-width="2.2" />',
        f'<line x1="{px_x(0)}" y1="{px_y(-3)}" x2="{px_x(9)}" y2="{px_y(-3)}" stroke="#ffffff" stroke-width="2.2" />',
        f'<line x1="{px_x(0)}" y1="{px_y(0)}" x2="{px_x(9)}" y2="{px_y(0)}" stroke="#09090b" stroke-width="5" />',
        f'<text x="{px_x(4.5)}" y="{px_y(0) + 16}" text-anchor="middle" fill="#6f6875" font-size="10" font-weight="700">NETZ</text>',
        f'<text x="{px_x(4.5)}" y="{px_y(-3) + 14}" text-anchor="middle" fill="#8b8490" font-size="9">3-M-LINIE</text>',
    ]
    marker_colors = {
        "ace": SERVICE_LINE_COLORS["ace"],
        "very-good": SERVICE_LINE_COLORS["very_good"],
        "neutral": SERVICE_LINE_COLORS["good"],
        "error": SERVICE_LINE_COLORS["error"],
    }
    arrow_markers = ["<defs>"]
    for marker_name, color in marker_colors.items():
        arrow_markers.append(
            f'<marker id="service-arrow-{marker_name}" markerWidth="5" markerHeight="5" '
            'refX="4.4" refY="2.5" orient="auto" markerUnits="userSpaceOnUse">'
            f'<path d="M0,0 L5,2.5 L0,5 Z" fill="{color}" /></marker>'
        )
    arrow_markers.append("</defs>")

    line_parts = []
    for index, action in enumerate(action_list):
        origin_x = int(action["service_origin_x"]) + 0.5
        target_x = int(action["landing_x"]) + 0.5
        target_y = int(action["landing_y"]) + 0.5
        jitter = ((int(action.get("id", index)) % 5) - 2) * 0.045
        result = str(action.get("service_result") or "okay")
        color = SERVICE_LINE_COLORS.get(result, SERVICE_LINE_COLORS["okay"])
        marker = (
            "ace"
            if result == "ace"
            else "very-good"
            if result == "very_good"
            else "error"
            if result == "error"
            else "neutral"
        )
        line_parts.append(
            f'<line data-service-result="{escape(result)}" '
            f'x1="{px_x(origin_x + jitter):.1f}" y1="{px_y(-9.2):.1f}" '
            f'x2="{px_x(target_x + jitter):.1f}" y2="{px_y(target_y):.1f}" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" opacity="0.72" '
            f'marker-end="url(#service-arrow-{marker})" />'
        )

    origin_parts = []
    for origin in sorted({int(action["service_origin_x"]) for action in action_list}):
        marker_x = px_x(origin + 0.5)
        marker_y = px_y(-9.2)
        origin_parts.append(
            f'<circle cx="{marker_x:.1f}" cy="{marker_y:.1f}" r="7" '
            'fill="#ffffff" stroke="#111014" stroke-width="1.2" />'
            f'<text x="{marker_x:.1f}" y="{marker_y + 2.8:.1f}" '
            f'text-anchor="middle" fill="#111014" font-size="7" font-weight="800">{origin + 1}</text>'
        )

    empty_note = (
        ""
        if action_list
        else '<text x="151" y="180" text-anchor="middle" fill="#6f6875" font-size="12">Noch keine Serviceorte gespeichert</text>'
    )
    return (
        '<div style="border:1px solid #e5e0e8;border-radius:16px;padding:.45rem;background:#fff">'
        '<svg viewBox="0 0 303 510" role="img" '
        f'aria-label="Servicerichtungen von {escape(player_name)}" style="display:block;width:100%;height:auto">'
        f'<text x="151" y="22" text-anchor="middle" fill="#111014" font-size="15" font-weight="800">Service · {len(action_list)}</text>'
        '<text x="151" y="40" text-anchor="middle" fill="#6f6875" font-size="10">Vom Serviceort ins gegnerische Feld</text>'
        + "".join(arrow_markers)
        + "".join(grid_parts)
        + "".join(line_parts)
        + "".join(origin_parts)
        + empty_note
        + "</svg></div>"
    )


def first_contact_target_label(cell_x: int, cell_y: int) -> str:
    if landing_cell_is_out(cell_x, cell_y):
        return "Annahmefehler · Ziel liegt im 1-m-Rand ausserhalb des Feldes"
    return f"Annahmeziel · {cell_x + 1}. Meter von links · {cell_y + 1}. Meter ab Netz"


FIRST_CONTACT_QUALITY_ZONES = (
    ("okay", "okay", "good", "good", "perfect", "perfect", "perfect", "good", "okay"),
    ("okay", "okay", "good", "good", "perfect", "perfect", "perfect", "good", "okay"),
    ("okay", "okay", "okay", "okay", "good", "good", "good", "okay", "okay"),
    ("bad", "bad", "bad", "okay", "okay", "okay", "okay", "okay", "okay"),
    *((("bad",) * 9) for _ in range(5)),
)


def suggested_first_contact_quality(cell_x: int, cell_y: int, *, too_low: bool = False) -> str:
    """Suggest reception quality from the fixed 9 x 9 target map and ball height."""

    if not (-1 <= int(cell_x) <= 9 and -1 <= int(cell_y) <= 9):
        raise ValueError("first-contact target must be inside the court or its 1-m error border")
    if landing_cell_is_out(cell_x, cell_y):
        return "error"
    quality = FIRST_CONTACT_QUALITY_ZONES[int(cell_y)][int(cell_x)]
    if not too_low:
        return quality
    return {
        "perfect": "good",
        "good": "okay",
        "okay": "bad",
        "bad": "bad",
    }[quality]


def group_set_origin_2x2(origin_x: float, origin_y: float) -> tuple[float, float]:
    """Group an exact reception target into a calmer 2 x 2 metre set origin."""

    def grouped_axis(value: float) -> float:
        bounded = max(0.0, min(9.0, float(value)))
        band_start = min(8.0, float(int(bounded // 2) * 2))
        band_end = min(9.0, band_start + 2.0)
        return (band_start + band_end) / 2.0

    return grouped_axis(origin_x), grouped_axis(origin_y)


def first_contact_target_chart(selected_cell: tuple[int, int] | None = None) -> alt.Chart:
    cells = []
    for cell_y in range(-1, 10):
        for cell_x in range(-1, 10):
            is_selected = selected_cell == (cell_x, cell_y)
            cells.append(
                {
                    "cell_x": cell_x,
                    "cell_y": cell_y,
                    "x0": cell_x,
                    "x1": cell_x + 1,
                    "y0": cell_y,
                    "y1": cell_y + 1,
                    "fill": (
                        "#7561a8"
                        if is_selected
                        else "#ef8b86"
                        if landing_cell_is_out(cell_x, cell_y)
                        else "#F6B4CD"
                    ),
                    "border": "#111014" if is_selected else "#ffffff",
                    "border_width": 2.4 if is_selected else 0.65,
                    "zone": first_contact_target_label(cell_x, cell_y),
                }
            )
    selection = alt.selection_point(
        name="first_contact_cell",
        fields=["cell_x", "cell_y"],
        on="click",
        toggle=False,
        clear=False,
    )
    grid = (
        alt.Chart(alt.Data(values=cells))
        .mark_rect()
        .encode(
            x=alt.X(
                "x0:Q",
                scale=alt.Scale(domain=[-1, 10], nice=False),
                axis=alt.Axis(
                    title="Breite · Meter",
                    values=list(range(-1, 11)),
                    grid=False,
                ),
            ),
            x2="x1:Q",
            y=alt.Y(
                "y0:Q",
                scale=alt.Scale(domain=[10, -1], nice=False),
                axis=alt.Axis(
                    title="Entfernung ab Netz · Meter",
                    values=list(range(-1, 11)),
                    grid=False,
                ),
            ),
            y2="y1:Q",
            color=alt.condition(
                selection,
                alt.value("#7561a8"),
                alt.Color("fill:N", scale=None, legend=None),
            ),
            stroke=alt.condition(
                selection,
                alt.value("#111014"),
                alt.Stroke("border:N", scale=None, legend=None),
            ),
            strokeWidth=alt.condition(
                selection,
                alt.value(2.4),
                alt.StrokeWidth("border_width:Q", legend=None),
            ),
            tooltip=[alt.Tooltip("zone:N", title="Annahmeziel")],
        )
        .add_params(selection)
    )
    court_boundary = (
        alt.Chart(alt.Data(values=[{"x0": 0, "x1": 9, "y0": 0, "y1": 9}]))
        .mark_rect(fillOpacity=0, stroke="#ffffff", strokeWidth=2.2)
        .encode(x="x0:Q", x2="x1:Q", y="y0:Q", y2="y1:Q")
    )
    attack_line = (
        alt.Chart(alt.Data(values=[{"x0": 0, "x1": 9, "line": 3}]))
        .mark_rule(color="#ffffff", strokeWidth=2.2)
        .encode(x="x0:Q", x2="x1:Q", y="line:Q")
    )
    net = alt.Chart(alt.Data(values=[{"net": 0}])).mark_rule(color="#09090b", strokeWidth=6).encode(y="net:Q")
    return (grid + court_boundary + attack_line + net).properties(width=440, height=440)


def reception_heatmap_svg(title: str, actions: Iterable[dict[str, Any]]) -> str:
    """Draw reception targets with guaranteed square 1×1 metre cells."""

    counts: dict[tuple[int, int], int] = {}
    for action in actions:
        if action.get("first_contact_x") is None or action.get("first_contact_y") is None:
            continue
        cell = (int(action["first_contact_x"]), int(action["first_contact_y"]))
        if not (0 <= cell[0] <= 8 and 0 <= cell[1] <= 8):
            continue
        counts[cell] = counts.get(cell, 0) + 1

    maximum = max(counts.values(), default=0)

    def blend(start: str, end: str, fraction: float) -> str:
        start_rgb = tuple(int(start[index : index + 2], 16) for index in (1, 3, 5))
        end_rgb = tuple(int(end[index : index + 2], 16) for index in (1, 3, 5))
        mixed = tuple(round(a + (b - a) * fraction) for a, b in zip(start_rgb, end_rgb))
        return "#" + "".join(f"{value:02x}" for value in mixed)

    def heat_color(value: int) -> str:
        ratio = value / maximum if maximum else 0.0
        if ratio <= 0.5:
            return blend("#fff8fb", "#F6B4CD", ratio * 2)
        return blend("#F6B4CD", "#7561a8", (ratio - 0.5) * 2)

    field_x = 48
    field_y = 70
    cell_size = 36
    field_size = cell_size * 9
    cell_parts: list[str] = []
    for cell_y in range(9):
        for cell_x in range(9):
            count = counts.get((cell_x, cell_y), 0)
            x = field_x + cell_x * cell_size
            y = field_y + cell_y * cell_size
            label = escape(f"{first_contact_target_label(cell_x, cell_y)}: {count} Annahmen")
            cell_parts.append(
                f'<g aria-label="{label}"><title>{label}</title>'
                f'<rect x="{x}" y="{y}" width="{cell_size}" height="{cell_size}" '
                f'fill="{heat_color(count)}" stroke="#ffffff" stroke-width="0.8" />'
                "</g>"
            )

    tick_parts = []
    for meter in range(1, 10, 2):
        center = field_x + (meter - 0.5) * cell_size
        tick_parts.append(
            f'<text x="{center:.1f}" y="{field_y + field_size + 17}" text-anchor="middle" '
            f'fill="#6f6875" font-size="9">{meter}</text>'
        )
        center_y = field_y + (meter - 0.5) * cell_size
        tick_parts.append(
            f'<text x="{field_x - 10}" y="{center_y + 3:.1f}" text-anchor="end" '
            f'fill="#6f6875" font-size="9">{meter}</text>'
        )

    return (
        '<div style="border:1px solid #e5e0e8;border-radius:16px;padding:.55rem;background:#fff;max-width:540px;margin:0 auto">'
        '<svg viewBox="0 0 420 445" role="img" '
        f'aria-label="Annahme-Heatmap {escape(title)}" style="display:block;width:100%;height:auto">'
        '<defs><linearGradient id="reception-heat-gradient" x1="0" x2="1">'
        '<stop offset="0%" stop-color="#fff8fb" /><stop offset="50%" stop-color="#F6B4CD" />'
        '<stop offset="100%" stop-color="#7561a8" /></linearGradient></defs>'
        f'<text x="210" y="21" text-anchor="middle" fill="#111014" font-size="15" font-weight="800">{escape(title)}</text>'
        '<text x="48" y="43" fill="#6f6875" font-size="9">weniger</text>'
        '<rect x="91" y="34" width="190" height="10" rx="5" fill="url(#reception-heat-gradient)" />'
        '<text x="288" y="43" fill="#6f6875" font-size="9">mehr</text>'
        + "".join(cell_parts)
        + f'<rect x="{field_x}" y="{field_y}" width="{field_size}" height="{field_size}" '
        'fill="none" stroke="#111014" stroke-width="1.6" />'
        + f'<line x1="{field_x}" y1="{field_y}" x2="{field_x + field_size}" y2="{field_y}" '
        'stroke="#09090b" stroke-width="6" />' + f'<line x1="{field_x}" y1="{field_y + 3 * cell_size}" '
        f'x2="{field_x + field_size}" y2="{field_y + 3 * cell_size}" '
        'stroke="#ffffff" stroke-width="2.2" />'
        + "".join(tick_parts)
        + f'<text x="{field_x + field_size / 2:.1f}" y="{field_y + field_size + 35}" '
        'text-anchor="middle" fill="#6f6875" font-size="10">Breite · Meter</text>'
        + '<text x="15" y="232" text-anchor="middle" fill="#6f6875" font-size="10" '
        'transform="rotate(-90 15 232)">Entfernung ab Netz · Meter</text>' + "</svg></div>"
    )


def performance_timeline_html(
    player_name: str,
    metrics: dict[str, list[dict[str, Any]]],
) -> str:
    """Render every metric as one continuous colour bar across the selected period."""

    rows: list[str] = []
    for metric in PERFORMANCE_METRIC_LABELS:
        events = metrics.get(metric, [])
        if not events:
            continue
        first_set = int(events[0]["set_number"])
        starts_on_court = events[0].get("on_court") is not False
        start_background = PERFORMANCE_LEVEL_COLORS[3] if starts_on_court else PERFORMANCE_BENCH_COLOR
        start_label = escape(f"Satz {first_set} · Startwert · mittlere Formstufe 3 von 5")
        cells: list[str] = [
            f'<span role="img" aria-label="{start_label}" title="{start_label}" '
            f'style="display:block;flex:1 1 0;min-width:2px;height:32px;'
            f'background:{start_background}"></span>'
        ]
        previous_set: int | None = None
        previous_color = PERFORMANCE_LEVEL_COLORS[3]
        for event in events:
            set_number = int(event["set_number"])
            rally_number = int(event["rally_number"])
            score = ""
            if event.get("our_score") is not None and event.get("opponent_score") is not None:
                score = f" · Stand {int(event['our_score'])}:{int(event['opponent_score'])}"
            shadows: list[str] = []
            if previous_set not in {None, set_number}:
                shadows.append("inset 4px 0 0 #111014")
            if metric == "service" and event.get("had_action"):
                shadows.append("inset 0 4px 0 #111014")
            marker_style = f"box-shadow:{','.join(shadows)};" if shadows else ""
            label = escape(
                f"Satz {set_number} · Punkt {rally_number}{score} · {event['detail']} · "
                f"Formstufe {event['level']} von 5"
            )
            cell_background = (
                f"linear-gradient(90deg,{previous_color} 0%,{event['color']} 100%)"
                if event.get("on_court") is not False
                else PERFORMANCE_BENCH_COLOR
            )
            cells.append(
                f'<span role="img" aria-label="{label}" title="{label}" '
                f'style="display:block;flex:1 1 0;min-width:2px;height:32px;'
                f'background:{cell_background};{marker_style}"></span>'
            )
            previous_set = set_number
            previous_color = str(event["color"])

        set_numbers = list(dict.fromkeys(int(event["set_number"]) for event in events))
        if len(set_numbers) == 1:
            target = int(events[0].get("target_points") or (15 if set_numbers[0] == 5 else 25))
            left_axis = f"Satz {set_numbers[0]} · Start"
            right_axis = f"{target}-Punkte-Skala"
        else:
            left_axis = "Matchbeginn"
            right_axis = "Matchende"
        marker_legend = (
            '<div style="display:flex;align-items:center;gap:.35rem;margin:.2rem 0 .35rem;'
            'font-size:.7rem;color:#6f6875"><span style="display:inline-block;width:18px;'
            'height:4px;background:#111014;border-radius:2px"></span>eigener Service</div>'
            if metric == "service"
            else ""
        )
        action_count = sum(int(bool(event.get("had_action", True))) for event in events)
        if any(event.get("on_court") is False for event in events):
            marker_legend += (
                '<div style="display:flex;align-items:center;gap:.35rem;margin:.2rem 0 .35rem;'
                'font-size:.7rem;color:#6f6875"><span style="display:inline-block;width:18px;'
                f'height:8px;background:{PERFORMANCE_BENCH_COLOR};border-radius:2px"></span>'
                "nicht auf dem Feld</div>"
            )
        rows.append(
            '<div style="margin:.7rem 0">'
            f'<div style="font-size:.86rem;font-weight:750;margin-bottom:.3rem">'
            f"{escape(PERFORMANCE_METRIC_LABELS[metric])} · {action_count} "
            f"{'Punkt mit Aktion' if action_count == 1 else 'Punkte mit Aktion'}</div>"
            + marker_legend
            + '<div style="display:flex;gap:0;width:100%;overflow:hidden;border-radius:999px;'
            'box-shadow:inset 0 0 0 1px rgba(17,16,20,.14)">' + "".join(cells) + "</div>"
            '<div style="display:flex;justify-content:space-between;margin-top:.22rem;'
            'font-size:.7rem;color:#6f6875">'
            f"<span>{escape(left_axis)}</span><span>{escape(right_axis)}</span>"
            "</div></div>"
        )

    legend_stops = ",".join(
        f"{color} {index * 100 / (len(PERFORMANCE_LEVEL_COLORS) - 1):.0f}%"
        for index, color in enumerate(PERFORMANCE_LEVEL_COLORS)
    )
    explanation_parts: list[str] = []
    if {"serve_reception", "defense_reception"}.intersection(metrics):
        explanation_parts.append(
            "Serviceannahme und Angriffs-/Gratisballabnahme laufen getrennt: jede zweite "
            "zu tiefe Serviceannahme, sonst jede dritte zu tiefe Abnahme ergibt -1."
        )
    if "attack" in metrics:
        explanation_parts.append(
            "Angriff: nach 6/10 anrechenbaren Teamangriffen ohne eigenen Angriff -1; "
            "nur auf dem Feld und ausser bei der Dia nur vorne auf P2/P3/P4."
        )
    if "setter_movement" in metrics:
        explanation_parts.append("Zuspieler: zweimal nacheinander rechtzeitig unter dem Ball ergibt +1.")
    if "set_location" in metrics:
        explanation_parts.append(
            "Passlage: Fehler sofort -1; sonst optimal +1; bei Grün nach 3, sonst nach 5 nicht optimalen Pässen -1."
        )
    explanation = " ".join(explanation_parts)
    if "block" in metrics:
        explanation += (
            " Blockform nur für Mitten: 5-mal zu langsam senkt um 1; "
            "7-mal Block zu oder 5 Blocktouches erhöhen um 1."
        )
    return (
        '<div style="border:1px solid #e5e0e8;border-radius:16px;padding:.8rem;background:#fff">'
        f'<div style="font-weight:850;font-size:1rem">{escape(player_name)}</div>'
        '<div style="display:flex;align-items:center;gap:.45rem;margin:.5rem 0 .8rem">'
        '<span style="font-size:.75rem;color:#6f6875">Formtief</span>'
        f'<span style="display:block;flex:1;max-width:260px;height:10px;border-radius:999px;'
        f'background:linear-gradient(90deg,{legend_stops})"></span>'
        '<span style="font-size:.75rem;color:#6f6875">Formhoch</span></div>'
        '<div style="font-size:.78rem;color:#6f6875;margin-bottom:.55rem">'
        f"{escape(explanation)}</div>" + "".join(rows) + "</div>"
    )


def performance_metrics_for_role(
    metrics: dict[str, list[dict[str, Any]]],
    role: str,
) -> dict[str, list[dict[str, Any]]]:
    """Hide role-inappropriate form categories without changing stored match data."""

    return {
        metric: events
        for metric, events in metrics.items()
        if not (role == "libero" and metric == "attack") and not (role != "middle" and metric == "block")
    }


def _match_player_role(session: dict[str, Any], player: Any) -> str:
    state = session.get("state", {})
    role = lineup_role_for_player(state.get("lineup_roles"), player.id)
    if role:
        return role
    for substitution in reversed(state.get("substitutions", [])):
        if player.id in {substitution.get("outgoing_id"), substitution.get("incoming_id")}:
            return str(substitution.get("role") or player.primary_position)
    return player.primary_position


def _played_match_players(
    session: dict[str, Any],
    actions: Iterable[dict[str, Any]],
    roster: tuple[Any, ...],
) -> list[Any]:
    played_ids = set(session.get("lineup_player_ids") or [])
    for substitution in session.get("state", {}).get("substitutions", []):
        played_ids.update(
            {
                substitution.get("outgoing_id"),
                substitution.get("incoming_id"),
            }
        )
    for action in actions:
        played_ids.update(
            action.get(field)
            for field in (
                "server_id",
                "receiver_id",
                "setter_id",
                "attacker_id",
                "block_player_id",
            )
        )
    return sorted(
        (player for player in roster if player.id in played_ids),
        key=lambda player: player.name,
    )


def _render_first_contact_target_picker(*, context: str) -> tuple[int, int] | None:
    value_key = f"{context}_first_contact_target_value"
    selected_cell = st.session_state.get(value_key)
    st.markdown("#### Wo landet die Annahme?")
    st.caption(
        "Netz oben · jedes Quadrat ist 1×1 m. Rosa ist das Feld; der rote 1-m-Rand "
        "bedeutet Annahmefehler. Im Feld bewertet die App den Zielort automatisch."
    )
    event = st.altair_chart(
        first_contact_target_chart(selected_cell),
        width="content",
        key=f"{context}_first_contact_target_chart",
        on_select="rerun",
        selection_mode="first_contact_cell",
    )
    selection_state = getattr(event, "selection", {})
    selected_points = selection_state.get("first_contact_cell", []) if selection_state else []
    if selected_points:
        point = selected_points[0]
        selected_cell = (int(point["cell_x"]), int(point["cell_y"]))
        st.session_state[value_key] = selected_cell
    if selected_cell is not None:
        target_label = first_contact_target_label(*selected_cell)
        if landing_cell_is_out(*selected_cell):
            st.error(target_label)
        else:
            st.success(target_label)
    return selected_cell


def _render_landing_picker(
    *,
    context: str,
    attack_origin: str,
    attack_type: str,
) -> tuple[int, int] | None:
    value_key = f"{context}_{attack_origin}_{attack_type}_landing_value"
    selected_cell = st.session_state.get(value_key)
    st.markdown("#### Wo landet der Ball?")
    st.caption(
        "Jedes Quadrat ist 1×1 m · Rosa = gegnerisches Feld · Grau = ein Meter Out-Zone · "
        "feine Linien = Meterraster · stärkere weisse Linie = 3-m-Linie · schwarze Linie unten = Netz"
    )
    event = st.altair_chart(
        landing_grid_chart(selected_cell),
        width="content",
        key=f"{context}_{attack_origin}_{attack_type}_landing_chart",
        on_select="rerun",
        selection_mode="landing_cell",
    )
    selection_state = getattr(event, "selection", {})
    selected_points = selection_state.get("landing_cell", []) if selection_state else []
    if selected_points:
        point = selected_points[0]
        selected_cell = (int(point["cell_x"]), int(point["cell_y"]))
        st.session_state[value_key] = selected_cell
    if selected_cell is None:
        st.warning("Bitte ein Zielfeld antippen.")
    else:
        st.success(landing_cell_label(*selected_cell))
        st.caption(f"Angriffsposition: {ATTACK_ORIGIN_LABELS.get(attack_origin, 'nicht bestimmt')}")
    return selected_cell


def _render_service_origin_picker(*, context: str) -> int | None:
    value_key = f"{context}_service_origin_value"
    st.markdown("#### Von wo wird serviert?")
    st.caption("Blick von hinter der Grundlinie · jeder Knopf entspricht einem 1-m-Abschnitt.")
    selected_cell = st.pills(
        "Serviceort hinter der Grundlinie",
        options=list(range(9)),
        format_func=lambda cell_x: f"{cell_x + 1} m",
        key=value_key,
    )
    if selected_cell is None:
        st.warning("Bitte den Serviceort auswählen.")
    else:
        st.success(service_origin_label(int(selected_cell)))
    return int(selected_cell) if selected_cell is not None else None


def _render_service_target_picker(*, context: str) -> tuple[int, int] | None:
    value_key = f"{context}_service_target_value"
    selected_cell = st.session_state.get(value_key)
    st.markdown("#### Wohin geht der Service?")
    st.caption(
        "Jedes Quadrat ist 1×1 m · Rosa = gegnerisches Feld · Grau = Out-Zone · schwarze Linie unten = Netz"
    )
    event = st.altair_chart(
        landing_grid_chart(selected_cell),
        width="content",
        key=f"{context}_service_target_chart",
        on_select="rerun",
        selection_mode="landing_cell",
    )
    selection_state = getattr(event, "selection", {})
    selected_points = selection_state.get("landing_cell", []) if selection_state else []
    if selected_points:
        point = selected_points[0]
        selected_cell = (int(point["cell_x"]), int(point["cell_y"]))
        st.session_state[value_key] = selected_cell
    if selected_cell is None:
        st.warning("Bitte das Serviceziel antippen.")
    else:
        st.success(landing_cell_label(*selected_cell))
    return selected_cell


def _render_service_placement_picker(
    *,
    context: str,
) -> tuple[int | None, tuple[int, int] | None]:
    origin_key = f"{context}_service_origin_value"
    target_key = f"{context}_service_target_value"
    selected_origin = st.session_state.get(origin_key)
    selected_target = st.session_state.get(target_key)
    st.markdown("#### Wohin geht der Service?")
    st.caption(
        "Jedes Quadrat ist 1×1 m · Rosa im Feld = Serviceziel · Grau = Out-Zone · "
        "schwarze Linie = Netz · pinker Balken ganz unten = Serviceort"
    )
    event = st.altair_chart(
        service_placement_chart(selected_target, selected_origin),
        width="content",
        key=f"{context}_service_placement_chart",
        on_select="rerun",
        selection_mode="service_cell",
    )
    selection_state = getattr(event, "selection", {})
    selected_points = selection_state.get("service_cell", []) if selection_state else []
    if selected_points:
        point = selected_points[0]
        if point.get("kind") == "origin":
            selected_origin = int(point["cell_x"])
            st.session_state[origin_key] = selected_origin
        else:
            selected_target = (int(point["cell_x"]), int(point["cell_y"]))
            st.session_state[target_key] = selected_target
    status_col_a, status_col_b = st.columns(2)
    if selected_origin is None:
        status_col_a.warning("Serviceort im pinken Balken antippen.")
    else:
        status_col_a.success(service_origin_label(int(selected_origin)))
    if selected_target is None:
        status_col_b.warning("Serviceziel im Feld antippen.")
    else:
        status_col_b.success(landing_cell_label(*selected_target))
    return (
        int(selected_origin) if selected_origin is not None else None,
        selected_target,
    )


def attack_result_for_block_outcome(block_outcome: str, normal_result: str | None) -> str | None:
    """Resolve the stored attack result from the opponent-block outcome."""

    forced_results = {
        "blockout": "point",
        "blocked_point": "error",
        "recycle_us": "continued",
        "touch_opponent": "continued",
    }
    if block_outcome not in ATTACK_BLOCK_OUTCOME_LABELS:
        raise ValueError("unknown attack block outcome")
    return forced_results.get(block_outcome, normal_result)


def _render_attack_result(
    *,
    context: str,
    selected_cell: tuple[int, int] | None,
    label: str,
    attack_block_outcome: str = "none",
) -> str | None:
    forced_result = attack_result_for_block_outcome(attack_block_outcome, None)
    if forced_result is not None:
        st.caption(
            f"Ergebnis automatisch: {ATTACK_RESULT_LABELS[forced_result]} · "
            f"{ATTACK_BLOCK_OUTCOME_LABELS[attack_block_outcome]}"
        )
        return forced_result

    is_out = bool(selected_cell and landing_cell_is_out(*selected_cell))
    options = ["error"] if is_out else list(ATTACK_RESULT_LABELS)
    if is_out:
        st.caption("Ziel liegt in der Out-Zone – das Ergebnis wird automatisch als Fehler gespeichert.")
    target_key = "none" if selected_cell is None else f"{selected_cell[0]}_{selected_cell[1]}"
    return st.segmented_control(
        label,
        options=options,
        default="error" if is_out else "continued",
        format_func=lambda value: ATTACK_RESULT_LABELS[value],
        disabled=is_out,
        key=f"{context}_attack_result_{target_key}",
    )


def _render_attack_block_outcome(*, context: str) -> str:
    return (
        st.pills(
            "Was passiert am gegnerischen Block?",
            options=list(ATTACK_BLOCK_OUTCOME_LABELS),
            default="none",
            format_func=lambda value: ATTACK_BLOCK_OUTCOME_LABELS[value],
            key=f"{context}_attack_block_outcome",
        )
        or "none"
    )


def attack_trajectory_svg(
    player_name: str,
    title: str,
    actions: Iterable[dict[str, Any]],
) -> str:
    action_list = [
        action
        for action in actions
        if action.get("attack_origin") in ATTACK_ORIGIN_COORDINATES
        and (
            (action.get("landing_x") is not None and action.get("landing_y") is not None)
            or action.get("attack_block_outcome") in {"blockout", "recycle_us", "blocked_point"}
        )
    ]

    def px_x(value: float) -> float:
        return 25 + (value + 1) * 23

    def px_y(value: float) -> float:
        return 280 - value * 23

    grid_parts = [
        '<rect x="25" y="50" width="253" height="253" fill="#e5e1e6" rx="4" />',
        f'<rect x="{px_x(0)}" y="{px_y(9)}" width="{9 * 23}" height="{9 * 23}" fill="#F6B4CD" stroke="#ffffff" stroke-width="2.2" />',
        f'<rect x="{px_x(0)}" y="{px_y(0)}" width="{9 * 23}" height="{9 * 23}" fill="#fff0f6" stroke="#d8d1dd" stroke-width="2.2" />',
        f'<line x1="{px_x(0)}" y1="{px_y(3)}" x2="{px_x(9)}" y2="{px_y(3)}" stroke="#ffffff" stroke-width="2.2" />',
        f'<line x1="{px_x(0)}" y1="{px_y(-3)}" x2="{px_x(9)}" y2="{px_y(-3)}" stroke="#ffffff" stroke-width="2.2" />',
        f'<line x1="{px_x(0)}" y1="{px_y(0)}" x2="{px_x(9)}" y2="{px_y(0)}" stroke="#09090b" stroke-width="5" />',
        f'<text x="{px_x(4.5)}" y="{px_y(0) + 16}" text-anchor="middle" fill="#6f6875" font-size="10" font-weight="700">NETZ</text>',
        f'<text x="{px_x(4.5)}" y="{px_y(-3) + 14}" text-anchor="middle" fill="#8b8490" font-size="9">3-M-LINIE</text>',
    ]

    arrow_markers = (
        "<defs>"
        '<marker id="attack-arrow-point" markerWidth="5" markerHeight="5" refX="4.4" refY="2.5" '
        'orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L5,2.5 L0,5 Z" fill="#15803d" /></marker>'
        '<marker id="attack-arrow-continued" markerWidth="5" markerHeight="5" refX="4.4" refY="2.5" '
        'orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L5,2.5 L0,5 Z" fill="#111014" /></marker>'
        '<marker id="attack-arrow-error" markerWidth="5" markerHeight="5" refX="4.4" refY="2.5" '
        'orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L5,2.5 L0,5 Z" fill="#dc2626" /></marker>'
        "</defs>"
    )

    line_parts = []
    for index, action in enumerate(action_list):
        origin_x, origin_y = ATTACK_ORIGIN_COORDINATES[action["attack_origin"]]
        if action.get("landing_x") is not None and action.get("landing_y") is not None:
            target_x = int(action["landing_x"]) + 0.5
            target_y = int(action["landing_y"]) + 0.5
        else:
            # Blockout, recycled balls and point blocks end at the net instead of a landing cell.
            target_x = origin_x
            target_y = 0.15
        jitter = ((int(action.get("id", index)) % 5) - 2) * 0.045
        attack_result = action.get("attack_result")
        color = ATTACK_LINE_COLORS.get(attack_result, "#111014")
        marker = attack_result if attack_result in ATTACK_LINE_COLORS else "continued"
        line_parts.append(
            f'<line x1="{px_x(origin_x + jitter):.1f}" y1="{px_y(origin_y):.1f}" '
            f'x2="{px_x(target_x + jitter):.1f}" y2="{px_y(target_y):.1f}" '
            f'stroke="{color}" stroke-width="2" stroke-linecap="round" opacity="0.72" '
            f'marker-end="url(#attack-arrow-{marker})" />'
        )

    origin_parts = []
    for origin in sorted({action["attack_origin"] for action in action_list}):
        origin_x, origin_y = ATTACK_ORIGIN_COORDINATES[origin]
        origin_parts.append(
            f'<circle cx="{px_x(origin_x):.1f}" cy="{px_y(origin_y):.1f}" r="9" '
            'fill="#ffffff" stroke="#111014" stroke-width="1.5" />'
            f'<text x="{px_x(origin_x):.1f}" y="{px_y(origin_y) + 3.5:.1f}" '
            f'text-anchor="middle" fill="#111014" font-size="8" font-weight="800">P{origin}</text>'
        )

    empty_note = (
        ""
        if action_list
        else '<text x="151" y="180" text-anchor="middle" fill="#6f6875" font-size="12">Noch keine Bälle gespeichert</text>'
    )
    return (
        '<div style="border:1px solid #e5e0e8;border-radius:16px;padding:.45rem;background:#fff">'
        '<svg viewBox="0 0 303 510" role="img" '
        f'aria-label="{escape(title)} von {escape(player_name)}" style="display:block;width:100%;height:auto">'
        f'<text x="151" y="22" text-anchor="middle" fill="#111014" font-size="15" font-weight="800">{escape(title)} · {len(action_list)}</text>'
        '<text x="151" y="40" text-anchor="middle" fill="#6f6875" font-size="10">Gegnerisches Feld</text>'
        + arrow_markers
        + "".join(grid_parts)
        + "".join(line_parts)
        + "".join(origin_parts)
        + empty_note
        + "</svg></div>"
    )


def pass_trajectory_color_tendencies(value: Any) -> tuple[str, ...]:
    """Return only the traits that control the visible trajectory colour."""

    tendencies = parse_set_tendencies(value)
    if "error" in tendencies:
        return ("error",)
    vertical = tuple(tendency for tendency in ("too_low", "too_high") if tendency in tendencies)
    if vertical:
        return vertical
    if "too_far_outside" in tendencies:
        return ("too_far_outside",)
    if "too_far_inside" in tendencies:
        return ("too_far_inside",)
    if "optimal" in tendencies:
        return ("optimal",)
    return ()


def pass_trajectory_svg(
    title: str,
    actions: Iterable[dict[str, Any]],
) -> str:
    """Draw set trajectories in a three-dimensional court projection."""

    action_list = [
        action
        for action in actions
        if action.get("set_origin_x") is not None
        and action.get("set_origin_y") is not None
        and action.get("attack_origin") in PASS_ATTACK_TARGETS_3D
        and pass_trajectory_color_tendencies(action.get("set_tendency"))
        and action.get("attack_type") not in {"setter_tip", "second_ball_return"}
    ]

    def project(x: float, y: float, z: float = 0.0) -> tuple[float, float]:
        """Oblique camera from the left baseline so depth and height stay visible."""

        return (
            62.0 + x * 31.0 + y * 13.0,
            285.0 + y * 9.8 - z * 43.0,
        )

    court_corners = [project(0, 0), project(9, 0), project(9, 9), project(0, 9)]
    court_polygon = " ".join(f"{x:.1f},{y:.1f}" for x, y in court_corners)
    court_parts = [f'<polygon points="{court_polygon}" fill="#fff0f6" stroke="#d8d1dd" stroke-width="2" />']
    for meter_y in range(10):
        left_x, line_y = project(0, meter_y)
        right_x, _ = project(9, meter_y)
        is_attack_line = meter_y == 3
        court_parts.append(
            f'<line x1="{left_x:.1f}" y1="{line_y:.1f}" x2="{right_x:.1f}" y2="{line_y:.1f}" '
            f'stroke="#ffffff" stroke-width="{2.8 if is_attack_line else 0.7}" '
            f'opacity="{0.95 if is_attack_line else 0.62}" />'
        )
    for meter_x in range(10):
        net_x, net_y = project(meter_x, 0)
        base_x, base_y = project(meter_x, 9)
        court_parts.append(
            f'<line x1="{net_x:.1f}" y1="{net_y:.1f}" x2="{base_x:.1f}" y2="{base_y:.1f}" '
            'stroke="#ffffff" stroke-width="0.7" opacity="0.55" />'
        )

    net_floor_left = project(0, 0, 0)
    net_floor_right = project(9, 0, 0)
    net_bottom_left = project(0, 0, WOMENS_NET_BOTTOM_METERS)
    net_bottom_right = project(9, 0, WOMENS_NET_BOTTOM_METERS)
    net_top_left = project(0, 0, WOMENS_NET_TOP_METERS)
    net_top_right = project(9, 0, WOMENS_NET_TOP_METERS)
    left_pole_top = project(0, 0, 2.65)
    right_pole_top = project(9, 0, 2.65)
    net_polygon = " ".join(
        f"{x:.1f},{y:.1f}" for x, y in (net_bottom_left, net_bottom_right, net_top_right, net_top_left)
    )
    net_parts = [
        f'<line x1="{net_floor_left[0]:.1f}" y1="{net_floor_left[1]:.1f}" '
        f'x2="{left_pole_top[0]:.1f}" y2="{left_pole_top[1]:.1f}" stroke="#111014" stroke-width="4" />',
        f'<line x1="{net_floor_right[0]:.1f}" y1="{net_floor_right[1]:.1f}" '
        f'x2="{right_pole_top[0]:.1f}" y2="{right_pole_top[1]:.1f}" stroke="#111014" stroke-width="4" />',
        f'<g class="volleyball-net" data-net-top-m="{WOMENS_NET_TOP_METERS:.2f}" '
        f'data-net-bottom-m="{WOMENS_NET_BOTTOM_METERS:.2f}" '
        'aria-label="Volleyballnetz mit Oberkante 2,24 Meter und Unterkante 1,24 Meter">',
        f'<polygon class="net-body" points="{net_polygon}" fill="#ffffff" fill-opacity="0.74" '
        'stroke="none" />',
    ]
    for height in net_mesh_heights():
        mesh_left = project(0, 0, height)
        mesh_right = project(9, 0, height)
        net_parts.append(
            f'<line class="net-mesh-horizontal" x1="{mesh_left[0]:.1f}" y1="{mesh_left[1]:.1f}" '
            f'x2="{mesh_right[0]:.1f}" y2="{mesh_right[1]:.1f}" '
            'stroke="#514b57" stroke-width="0.55" opacity="0.58" />'
        )
    for court_x in net_mesh_positions():
        mesh_bottom = project(court_x, 0, WOMENS_NET_BOTTOM_METERS)
        mesh_top = project(court_x, 0, WOMENS_NET_TOP_METERS)
        net_parts.append(
            f'<line class="net-mesh-vertical" x1="{mesh_bottom[0]:.1f}" y1="{mesh_bottom[1]:.1f}" '
            f'x2="{mesh_top[0]:.1f}" y2="{mesh_top[1]:.1f}" '
            'stroke="#514b57" stroke-width="0.55" opacity="0.58" />'
        )
    net_parts.extend(
        [
            f'<line class="net-bottom-cord" x1="{net_bottom_left[0]:.1f}" y1="{net_bottom_left[1]:.1f}" '
            f'x2="{net_bottom_right[0]:.1f}" y2="{net_bottom_right[1]:.1f}" '
            'stroke="#514b57" stroke-width="1.4" />',
            f'<line x1="{net_bottom_left[0]:.1f}" y1="{net_bottom_left[1]:.1f}" '
            f'x2="{net_top_left[0]:.1f}" y2="{net_top_left[1]:.1f}" stroke="#111014" stroke-width="4.5" />',
            f'<line class="net-side-band" x1="{net_bottom_left[0]:.1f}" y1="{net_bottom_left[1]:.1f}" '
            f'x2="{net_top_left[0]:.1f}" y2="{net_top_left[1]:.1f}" stroke="#ffffff" stroke-width="2.2" />',
            f'<line x1="{net_bottom_right[0]:.1f}" y1="{net_bottom_right[1]:.1f}" '
            f'x2="{net_top_right[0]:.1f}" y2="{net_top_right[1]:.1f}" stroke="#111014" stroke-width="4.5" />',
            f'<line class="net-side-band" x1="{net_bottom_right[0]:.1f}" y1="{net_bottom_right[1]:.1f}" '
            f'x2="{net_top_right[0]:.1f}" y2="{net_top_right[1]:.1f}" stroke="#ffffff" stroke-width="2.2" />',
            f'<line x1="{net_top_left[0]:.1f}" y1="{net_top_left[1]:.1f}" '
            f'x2="{net_top_right[0]:.1f}" y2="{net_top_right[1]:.1f}" stroke="#09090b" stroke-width="6" />',
            f'<line class="net-top-tape" x1="{net_top_left[0]:.1f}" y1="{net_top_left[1]:.1f}" '
            f'x2="{net_top_right[0]:.1f}" y2="{net_top_right[1]:.1f}" stroke="#ffffff" stroke-width="2.6" />',
            "</g>",
            '<text x="205" y="128" text-anchor="middle" fill="#111014" font-size="11" '
            'font-weight="800">NETZ</text>',
            '<text x="396" y="297" text-anchor="end" fill="#8b8490" font-size="9">3-M-LINIE</text>',
            '<text x="25" y="170" text-anchor="middle" fill="#6f6875" font-size="9" '
            'transform="rotate(-90 25 170)">BALLHÖHE</text>',
        ]
    )
    for height in (1, 2, 3):
        tick_x, tick_y = project(0, 0, height)
        net_parts.append(
            f'<line x1="{tick_x - 5:.1f}" y1="{tick_y:.1f}" x2="{tick_x + 2:.1f}" y2="{tick_y:.1f}" '
            'stroke="#6f6875" stroke-width="1" />'
            f'<text x="{tick_x - 9:.1f}" y="{tick_y + 3:.1f}" text-anchor="end" '
            f'fill="#6f6875" font-size="8">{height} m</text>'
        )

    defs = [
        '<marker id="pass-arrow" markerWidth="5" markerHeight="5" refX="4.4" refY="2.5" '
        'orient="auto" markerUnits="userSpaceOnUse"><path d="M0,0 L5,2.5 L0,5 Z" fill="#111014" />'
        "</marker>"
    ]
    path_parts: list[str] = []

    for index, action in enumerate(action_list):
        tendencies = parse_set_tendencies(action.get("set_tendency"))
        start_court_x, start_court_y = group_set_origin_2x2(
            float(action["set_origin_x"]),
            float(action["set_origin_y"]),
        )
        target_court_x, target_court_y, target_height = PASS_ATTACK_TARGETS_3D[action["attack_origin"]]
        inside_meters = float(action.get("set_inside_meters") or 0.0)

        if "too_far_inside" in tendencies:
            shift_meters = max(0.5, inside_meters)
            if target_court_x < 4.5:
                target_court_x += shift_meters
            elif target_court_x > 4.5:
                target_court_x -= shift_meters
            else:
                target_court_x += shift_meters if start_court_x >= target_court_x else -shift_meters
        if "too_far_outside" in tendencies:
            if target_court_x < 4.5:
                target_court_x = -1.4
            elif target_court_x > 4.5:
                target_court_x = 10.4
            else:
                target_court_x = -1.4 if start_court_x >= target_court_x else 10.4
        target_court_x = max(-1.6, min(10.6, target_court_x))

        if action["attack_origin"] == "3":
            if "too_low" in tendencies and "too_high" not in tendencies:
                # A low quick set should visibly skim the 2.24 m women's net.
                target_height = 2.31
                trajectory_lift = 0.12
            elif "too_high" in tendencies and "too_low" not in tendencies:
                target_height = 3.45
                trajectory_lift = 0.65
            else:
                # Optimal and purely lateral deviations keep the ideal quick-set height.
                target_height = 2.68
                trajectory_lift = 0.38
        elif "too_high" in tendencies and "too_low" not in tendencies:
            trajectory_lift = 3.15
        elif "too_low" in tendencies and "too_high" not in tendencies:
            trajectory_lift = 1.15
        else:
            trajectory_lift = 2.1

        jitter = ((int(action.get("id", index)) % 7) - 3) * 0.055
        projected_points = []
        for step in range(29):
            progress = step / 28
            court_x = (
                start_court_x
                + (target_court_x - start_court_x) * progress
                + 4 * progress * (1 - progress) * jitter
            )
            court_y = start_court_y + (target_court_y - start_court_y) * progress
            base_height = 2.15 + (target_height - 2.15) * progress
            ball_height = base_height + 4 * progress * (1 - progress) * trajectory_lift
            projected_points.append(project(court_x, court_y, ball_height))
        path_data = " ".join(
            ("M" if point_index == 0 else "L") + f" {point_x:.1f} {point_y:.1f}"
            for point_index, (point_x, point_y) in enumerate(projected_points)
        )
        start_x, start_y = projected_points[0]
        target_x, target_y = projected_points[-1]

        gradient_id = f"pass-gradient-{index}"
        color_tendencies = pass_trajectory_color_tendencies(tendencies)
        colors = [SET_TRAIT_COLORS[tendency] for tendency in color_tendencies]
        if len(colors) == 1:
            stops = (
                f'<stop offset="0%" stop-color="{colors[0]}" />'
                f'<stop offset="100%" stop-color="{colors[0]}" />'
            )
        else:
            stops = "".join(
                f'<stop offset="{100 * color_index / (len(colors) - 1):.0f}%" stop-color="{color}" />'
                for color_index, color in enumerate(colors)
            )
        defs.append(
            f'<linearGradient id="{gradient_id}" gradientUnits="userSpaceOnUse" '
            f'x1="{start_x:.1f}" y1="{start_y:.1f}" x2="{target_x:.1f}" y2="{target_y:.1f}">'
            f"{stops}</linearGradient>"
        )

        tendency_text = " + ".join(SET_TENDENCY_LABELS[value] for value in tendencies)
        if "too_far_inside" in tendencies and inside_meters:
            tendency_text += f" · {inside_meters:.1f} m innen"
        accessible_label = escape(
            f"{action.get('setter_name') or 'Zuspieler'} vom 2×2-m-Zuspielbereich "
            f"{start_court_x:.1f}/{start_court_y:.1f} Meter zu "
            f"{action.get('attacker_name') or 'Angreifer'} auf P{action['attack_origin']}: "
            f"{tendency_text}"
        )
        path_parts.append(
            f'<path d="{path_data}" fill="none" stroke="url(#{gradient_id})" '
            f'stroke-width="3" stroke-linecap="round" opacity="0.72" '
            f'data-attack-origin="{action["attack_origin"]}" data-target-height="{target_height:.2f}" '
            f'marker-end="url(#pass-arrow)" aria-label="{accessible_label}"><title>{accessible_label}</title></path>'
        )

    origin_parts = []
    origin_coordinates = sorted(
        {
            group_set_origin_2x2(
                float(action["set_origin_x"]),
                float(action["set_origin_y"]),
            )
            for action in action_list
        }
    )
    for court_x, court_y in origin_coordinates:
        floor_x, floor_y = project(court_x, court_y, 0)
        x, y = project(court_x, court_y, 2.15)
        origin_label = escape(f"Zuspielbereich {court_x:.1f}/{court_y:.1f} Meter")
        origin_parts.append(
            f'<g aria-label="{origin_label}"><title>{origin_label}</title>'
            f'<line x1="{floor_x:.1f}" y1="{floor_y:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
            'stroke="#7561a8" stroke-width="1" stroke-dasharray="3 3" opacity="0.55" />'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="8" fill="#F6B4CD" '
            'stroke="#7561a8" stroke-width="2" /></g>'
        )

    target_parts = []
    for position in sorted({action["attack_origin"] for action in action_list}):
        court_x, court_y, height = PASS_ATTACK_TARGETS_3D[position]
        floor_x, floor_y = project(court_x, court_y, 0)
        x, y = project(court_x, court_y, height)
        target_parts.append(
            f'<line x1="{floor_x:.1f}" y1="{floor_y:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
            'stroke="#111014" stroke-width="1" stroke-dasharray="3 3" opacity="0.35" />'
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="10" fill="#ffffff" '
            'stroke="#111014" stroke-width="1.5" />'
            f'<text x="{x:.1f}" y="{y + 3.5:.1f}" text-anchor="middle" fill="#111014" '
            f'font-size="8" font-weight="800">P{position}</text>'
        )

    empty_note = (
        ""
        if action_list
        else '<text x="260" y="255" text-anchor="middle" fill="#6f6875" font-size="12">Noch keine Passflugbahnen gespeichert</text>'
    )
    return (
        '<div style="border:1px solid #e5e0e8;border-radius:16px;padding:.55rem;background:#fff;max-width:760px;margin:0 auto">'
        '<svg viewBox="0 0 520 430" role="img" '
        f'aria-label="3D-Passflugbahnen {escape(title)}" style="display:block;width:100%;height:auto">'
        f'<text x="260" y="22" text-anchor="middle" fill="#111014" font-size="15" font-weight="800">{escape(title)} · {len(action_list)}</text>'
        '<text x="260" y="42" text-anchor="middle" fill="#6f6875" font-size="10">Schräge Seitenansicht von links hinten Richtung Netz</text>'
        + "".join(court_parts)
        + "".join(net_parts)
        + "<defs>"
        + "".join(defs)
        + "</defs>"
        + "".join(path_parts)
        + "".join(target_parts)
        + "".join(origin_parts)
        + empty_note
        + "</svg></div>"
    )


def build_live_court_svg(
    state: dict[str, Any],
    roster: Iterable[Any],
    *,
    show_serve_receive: bool | None = None,
) -> str:
    players = _players_by_id(roster)
    is_serve_receive = (
        state.get("phase") == "serve_receive" if show_serve_receive is None else show_serve_receive
    )
    rotation = int(
        state.get("current_rotation")
        or setter_rotation_position(
            state.get("rotation_slots", state.get("positions", {})),
            state.get("lineup_roles"),
        )
        or 1
    )
    if is_serve_receive:
        displayed_positions = dict(state.get("positions", {}))
        court_coordinates = serve_receive_court_coordinates(rotation)
    else:
        displayed_positions = system_court_positions(state)
        court_coordinates = COURT_COORDINATES
    rotation_position_by_player = {
        player_id: position for position, player_id in state.get("positions", {}).items()
    }
    markers: list[str] = []
    for displayed_position in ("4", "3", "2", "5", "6", "1"):
        player = players.get(displayed_positions.get(displayed_position))
        if player is None:
            continue
        rotation_position = rotation_position_by_player.get(player.id, displayed_position)
        x, y = court_coordinates[displayed_position]
        color = PLAYER_COLORS.get(player.primary_position, "#475569")
        name = escape(player.name)
        marker_label = (
            f"Annahmeplatz Läufer L{rotation}, Rotationsposition {rotation_position}: {name}"
            if is_serve_receive
            else f"Systemposition {displayed_position}, Rotationsposition {rotation_position}: {name}"
        )
        markers.append(
            f'<g aria-label="{marker_label}">'
            f'<circle cx="{x}" cy="{y}" r="31" fill="{color}" stroke="#ffffff" stroke-width="3" />'
            f'<text x="{x}" y="{y - 3}" text-anchor="middle" fill="#ffffff" '
            f'font-size="11" font-weight="700">P{rotation_position}</text>'
            f'<text x="{x}" y="{y + 14}" text-anchor="middle" fill="#ffffff" '
            f'font-size="12" font-weight="700">{name}</text>'
            "</g>"
        )
    return (
        '<div style="max-width:360px;margin:0 auto 1rem auto">'
        f'<svg viewBox="0 0 340 365" role="img" aria-label="'
        f'{"Serviceannahme" if is_serve_receive else "Aktuelle Systemaufstellung"} von oben" '
        'style="display:block;width:100%;height:auto">'
        '<text x="170" y="16" text-anchor="middle" fill="#6f6875" font-size="12" font-weight="700">NETZ</text>'
        '<rect x="10" y="25" width="320" height="320" rx="5" fill="#f3eff7" stroke="#d8d1dd" stroke-width="4" />'
        '<line x1="10" y1="25" x2="330" y2="25" stroke="#09090b" stroke-width="7" />'
        '<line x1="10" y1="132" x2="330" y2="132" stroke="#ffffff" stroke-width="3" />'
        + "".join(markers)
        + "</svg></div>"
    )


def _render_live_court(
    state: dict[str, Any],
    roster: tuple[Any, ...],
    *,
    show_serve_receive: bool | None = None,
) -> None:
    if not state.get("positions"):
        return
    is_serve_receive = (
        state.get("phase") == "serve_receive" if show_serve_receive is None else show_serve_receive
    )
    if is_serve_receive:
        st.markdown(f"#### Serviceannahme · Läufer L{state.get('current_rotation', 1)}")
    else:
        st.markdown("#### Aktuelle Systemaufstellung")
    st.markdown(
        build_live_court_svg(
            state,
            roster,
            show_serve_receive=is_serve_receive,
        ),
        unsafe_allow_html=True,
    )
    if is_serve_receive:
        st.caption(
            "Position für den ersten Ball gemäss euren sechs Annahmeformationen · Nummer im Kreis = "
            "Rotationsposition · Rechts/links und vorne/hinten bleiben regelkonform. Ab dem Serviceanwurf "
            "dürfen die Annahmespieler ihre Rotationsposition verlassen."
        )
        if int(state.get("current_rotation") or 0) == 1:
            st.info(
                "L1-Sonderregel: Nach der Annahme greift die Aussen auf P2 und die Dia auf P4 an. "
                "Das bleibt so, bis ihr das Servicerecht gewinnt und zu L6 rotiert."
            )
    else:
        st.caption(
            "Nummer im Kreis = Rotationsposition · Platz auf dem Feld = Systemposition nach dem ersten Ball."
        )


def _default_lineup(roster: tuple[Any, ...]) -> list[Any]:
    selected: list[Any] = []
    used: set[str] = set()

    def take(position: str, count: int) -> None:
        for player in roster:
            if len([item for item in selected if item.primary_position == position]) >= count:
                return
            if player.id in used or player.primary_position != position:
                continue
            selected.append(player)
            used.add(player.id)

    take("setter", 1)
    take("opposite", 1)
    take("outside", 2)
    take("middle", 2)
    take("libero", 1)
    if len(selected) == 7 and assign_lineup_roles(selected) is not None:
        return selected

    # Secondary positions may complete a deliberately flexible Herren-1 roster.
    for player in roster:
        if player.id not in used:
            selected.append(player)
        if len(selected) >= 7 and assign_lineup_roles(selected[:7]) is not None:
            return selected[:7]
    return []


def _lineup_from_system_book_metadata(
    roster: tuple[Any, ...],
    raw_value: str | None,
) -> tuple[list[Any], dict[str, Any] | None]:
    """Load a complete, current and position-valid Systembuch assignment."""

    if not raw_value:
        return [], None
    try:
        payload = json.loads(raw_value)
    except (TypeError, ValueError):
        return [], None
    if not isinstance(payload, dict) or not isinstance(payload.get("lineup_roles"), dict):
        return [], None

    saved_roles = payload["lineup_roles"]
    outsides = saved_roles.get("outsides")
    middles = saved_roles.get("middles")
    if not isinstance(outsides, list) or len(outsides) != 2:
        return [], None
    if not isinstance(middles, list) or len(middles) != 2:
        return [], None
    lineup_roles = {
        "setter": saved_roles.get("setter"),
        "opposite": saved_roles.get("opposite"),
        "outsides": list(outsides),
        "middles": list(middles),
        "libero": saved_roles.get("libero"),
    }
    player_ids = [
        lineup_roles["setter"],
        lineup_roles["opposite"],
        *lineup_roles["outsides"],
        *lineup_roles["middles"],
        lineup_roles["libero"],
    ]
    players_by_id = _players_by_id(roster)
    if (
        any(not isinstance(player_id, str) or not player_id for player_id in player_ids)
        or len(set(player_ids)) != 7
        or any(player_id not in players_by_id for player_id in player_ids)
    ):
        return [], None

    lineup = [players_by_id[player_id] for player_id in player_ids]
    assigned_roles = assign_lineup_roles(lineup, preferred_roles=lineup_roles)
    if assigned_roles != lineup_roles:
        return [], None
    return lineup, assigned_roles


def _match_setup_lineup_defaults(
    roster: tuple[Any, ...],
) -> tuple[list[Any], dict[str, Any] | None]:
    saved_lineup, saved_roles = _lineup_from_system_book_metadata(
        roster,
        get_app_metadata(SYSTEM_BOOK_LINEUP_METADATA_KEY),
    )
    if saved_roles is not None:
        return saved_lineup, saved_roles
    return _default_lineup(roster), None


def _default_player(players: list[Any], positions: tuple[str, ...]) -> Any:
    for position in positions:
        for player in players:
            if player.primary_position == position:
                return player
    return players[0]


def _session_label(session: dict[str, Any]) -> str:
    state = session["state"]
    video_prefix = f"{session['video_title']} · " if session.get("video_title") else ""
    return (
        f"{video_prefix}{session['match_date']} · {session['opponent']} · "
        f"Sätze {state.get('our_sets', 0)}:{state.get('opponent_sets', 0)}"
    )


def _save_state(session_id: int, state: dict[str, Any], lineup_player_ids: list[str] | None = None) -> None:
    update_match_session(session_id=session_id, state=state, lineup_player_ids=lineup_player_ids)


def _video_point_label(target: tuple[int, int]) -> str:
    return f"Satz {target[0]} · Punkt {target[1]}"


def _youtube_video_id(video_url: str) -> str:
    normalized = normalize_youtube_url(video_url)
    return parse_qs(urlparse(normalized).query)["v"][0] if normalized else ""


def _video_fallback_title(video_url: str) -> str:
    video_id = _youtube_video_id(video_url)
    return f"YouTube · {video_id}" if video_id else "Video ohne Titel"


def _video_source_sessions() -> list[dict[str, Any]]:
    sources = []
    for candidate in list_match_sessions():
        if not candidate.get("video_url"):
            continue
        segment_count = len(list_match_video_segments(session_id=int(candidate["id"])))
        if not segment_count:
            continue
        source = dict(candidate)
        source["segment_count"] = segment_count
        sources.append(source)
    return sources


def _video_source_label(session: dict[str, Any] | None) -> str:
    if session is None:
        return "Ohne Video analysieren"
    title = session.get("video_title") or _video_fallback_title(session.get("video_url", ""))
    segment_count = int(session.get("segment_count", 0))
    point_label = "Punkt" if segment_count == 1 else "Punkte"
    return f"{title} · {session['match_date']} · {session['opponent']} · {segment_count} {point_label}"


def _render_video_link_editor(session: dict[str, Any], *, key_prefix: str) -> None:
    session_id = int(session["id"])
    st.caption(
        "Öffentliche und nicht gelistete YouTube-Videos funktionieren. Ohne Link kannst du "
        "die Matchanalyse weiterhin normal verwenden."
    )
    url_value = st.text_input(
        "YouTube-Link",
        value=session.get("video_url", ""),
        placeholder="https://www.youtube.com/watch?v=…",
        key=f"{key_prefix}_video_url_{session_id}",
    )
    title_value = st.text_input(
        "Videotitel",
        value=session.get("video_title", ""),
        placeholder="Wird beim Laden automatisch aus YouTube übernommen",
        key=f"{key_prefix}_video_title_{session_id}",
    )
    save_url, remove_url = st.columns([2, 1])
    if save_url.button(
        "Videolink speichern",
        type="primary",
        width="stretch",
        key=f"{key_prefix}_save_video_url_{session_id}",
    ):
        try:
            normalized_url = normalize_youtube_url(url_value)
        except ValueError as error:
            st.error(str(error))
        else:
            if not normalized_url:
                st.error("Bitte zuerst einen YouTube-Link einfügen.")
            else:
                saved_title = title_value.strip()
                if not saved_title and normalized_url != session.get("video_url"):
                    saved_title = _video_fallback_title(normalized_url)
                update_match_video_url(
                    session_id=session_id,
                    video_url=normalized_url,
                    video_title=saved_title or session.get("video_title", ""),
                )
                st.rerun(scope="app")
    if remove_url.button(
        "Link entfernen",
        disabled=not bool(session.get("video_url")),
        width="stretch",
        key=f"{key_prefix}_remove_video_url_{session_id}",
    ):
        update_match_video_url(session_id=session_id, video_url="", video_title="")
        st.rerun(scope="app")


def _video_cut_state(session: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(session.get("state", {}).get("video_cut_state") or new_video_cut_state())


def _render_video_cut_scoreboard(session: dict[str, Any], cut_state: dict[str, Any]) -> None:
    st.markdown(f"### Satz {cut_state['current_set']} · Punkt {cut_state['rally_number']} schneiden")
    our_score, sets, opponent_score = st.columns([2, 1, 2])
    our_score.metric(TEAM_NAME, cut_state["our_score"])
    sets.metric("Sätze", f"{cut_state['our_sets']} : {cut_state['opponent_sets']}")
    opponent_score.metric(session["opponent"], cut_state["opponent_score"])
    if cut_state.get("completed_sets"):
        st.caption(
            " · ".join(
                f"Satz {item['set_number']}: {item['our_score']}:{item['opponent_score']}"
                for item in cut_state["completed_sets"]
            )
        )


def _clear_pending_video_marker(
    *,
    action_key: str,
    seconds_key: str,
    resume_token_key: str,
) -> None:
    st.session_state.pop(action_key, None)
    st.session_state.pop(seconds_key, None)
    st.session_state[resume_token_key] += 1


def _video_event_rows(
    session: dict[str, Any],
    video_events: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for item in video_events:
        if item["event_type"] == "timeout":
            side_name = TEAM_NAME if item["side"] == "us" else session["opponent"]
            description = f"Timeout · {side_name}"
        else:
            description = f"Wechsel · {item['outgoing_name']} raus · {item['incoming_name']} rein"
        rows.append(
            {
                "Satz": item["set_number"],
                "Stand": f"{item['our_score']}:{item['opponent_score']}",
                "Vor Punkt": item["rally_number"],
                "Videozeit": format_video_timestamp(int(item["video_seconds"])),
                "Ereignis": description,
            }
        )
    return rows


def _render_video_marker_form(
    session: dict[str, Any],
    roster: tuple[Any, ...],
    cut_state: dict[str, Any],
    *,
    marker_action: str,
    marker_seconds: int,
    action_key: str,
    seconds_key: str,
    resume_token_key: str,
) -> None:
    session_id = int(session["id"])
    score_text = f"{cut_state['our_score']}:{cut_state['opponent_score']}"
    st.markdown(f"#### Markierung bei {format_video_timestamp(marker_seconds)} · Stand {score_text}")

    def save_marker(**details: Any) -> None:
        save_match_video_event(
            session_id=session_id,
            event_type=marker_action,
            video_seconds=marker_seconds,
            set_number=int(cut_state["current_set"]),
            rally_number=int(cut_state["rally_number"]),
            our_score=int(cut_state["our_score"]),
            opponent_score=int(cut_state["opponent_score"]),
            **details,
        )
        _clear_pending_video_marker(
            action_key=action_key,
            seconds_key=seconds_key,
            resume_token_key=resume_token_key,
        )
        st.rerun(scope="app")

    if marker_action == "timeout":
        st.caption("Wer hat das Timeout genommen?")
        our_timeout, opponent_timeout, cancel_column = st.columns([2, 2, 1])
        if our_timeout.button(
            TEAM_NAME,
            type="primary",
            width="stretch",
            key=f"video_timeout_us_{session_id}_{marker_seconds}",
        ):
            save_marker(side="us")
        if opponent_timeout.button(
            session["opponent"],
            width="stretch",
            key=f"video_timeout_opponent_{session_id}_{marker_seconds}",
        ):
            save_marker(side="opponent")
        if cancel_column.button(
            "Abbrechen",
            width="stretch",
            key=f"video_timeout_cancel_{session_id}_{marker_seconds}",
        ):
            _clear_pending_video_marker(
                action_key=action_key,
                seconds_key=seconds_key,
                resume_token_key=resume_token_key,
            )
            st.rerun(scope="app")
        return

    player_ids = [player.id for player in roster]
    players = _players_by_id(roster)
    with st.form(f"video_substitution_form_{session_id}_{marker_seconds}"):
        outgoing_id = st.selectbox(
            "Spieler raus",
            options=["", *player_ids],
            format_func=lambda player_id: players[player_id].name if player_id else "Bitte auswählen",
        )
        incoming_id = st.selectbox(
            "Spieler rein",
            options=["", *player_ids],
            format_func=lambda player_id: players[player_id].name if player_id else "Bitte auswählen",
        )
        save_column, cancel_column = st.columns([2, 1])
        save_substitution = save_column.form_submit_button(
            "Wechsel speichern",
            type="primary",
            width="stretch",
        )
        cancel_substitution = cancel_column.form_submit_button(
            "Abbrechen",
            width="stretch",
        )
    if cancel_substitution:
        _clear_pending_video_marker(
            action_key=action_key,
            seconds_key=seconds_key,
            resume_token_key=resume_token_key,
        )
        st.rerun(scope="app")
    if save_substitution:
        if not outgoing_id or not incoming_id:
            st.error("Bitte den Spieler auswählen, der rausgeht, und den Spieler, der reinkommt.")
        elif outgoing_id == incoming_id:
            st.error("Die ein- und ausgewechselte Spieler müssen verschieden sein.")
        else:
            save_marker(
                side="us",
                outgoing_id=outgoing_id,
                outgoing_name=players[outgoing_id].name,
                incoming_id=incoming_id,
                incoming_name=players[incoming_id].name,
            )


def _render_video_cutter(session: dict[str, Any], roster: tuple[Any, ...]) -> None:
    session_id = int(session["id"])
    cut_state = _video_cut_state(session)
    _render_video_cut_scoreboard(session, cut_state)
    if st.button(
        "Anderes Video oder Match öffnen",
        width="stretch",
        key=f"leave_video_cut_{session_id}",
    ):
        st.session_state.pop("live_match_session_id", None)
        st.rerun(scope="app")
    with st.expander("YouTube-Video ändern", expanded=not bool(session.get("video_url"))):
        _render_video_link_editor(session, key_prefix="cut")
    video_url = session.get("video_url", "")
    if not video_url:
        st.info(
            "Füge oben zuerst den YouTube-Link ein. Wenn du ohne Video analysieren willst, "
            "wechsle direkt zu «Punkte analysieren»."
        )
        return

    segments = list_match_video_segments(session_id=session_id)
    video_events = list_match_video_events(session_id=session_id)
    last_segment_end = int(segments[-1]["end_seconds"]) if segments else 0
    last_event_time = int(video_events[-1]["video_seconds"]) if video_events else 0
    last_end = max(last_segment_end, last_event_time)
    pending_start_key = f"video_cut_pending_start_{session_id}"
    pending_end_key = f"video_cut_pending_end_{session_id}"
    event_nonce_key = f"video_cut_event_nonce_{session_id}"
    resume_token_key = f"video_cut_resume_token_{session_id}"
    seek_token_key = f"video_cut_seek_token_{session_id}"
    seek_seconds_key = f"video_cut_seek_seconds_{session_id}"
    marker_action_key = f"video_cut_marker_action_{session_id}"
    marker_seconds_key = f"video_cut_marker_seconds_{session_id}"
    st.session_state.setdefault(resume_token_key, 0)
    st.session_state.setdefault(seek_token_key, 0)
    st.session_state.setdefault(seek_seconds_key, last_end)

    event = YOUTUBE_CUTTER(
        video_id=_youtube_video_id(video_url),
        video_title=session.get("video_title", ""),
        start_seconds=int(st.session_state[seek_seconds_key]),
        pending_start=st.session_state.get(pending_start_key),
        pending_end=st.session_state.get(pending_end_key),
        pending_marker=st.session_state.get(marker_action_key),
        resume_token=int(st.session_state[resume_token_key]),
        seek_token=int(st.session_state[seek_token_key]),
        key=f"youtube_cutter_{session_id}",
        default=None,
    )
    if isinstance(event, dict) and event.get("nonce") != st.session_state.get(event_nonce_key):
        st.session_state[event_nonce_key] = event.get("nonce")
        if event.get("action") == "metadata":
            youtube_title = str(event.get("title") or "").strip()
            if youtube_title and youtube_title != session.get("video_title"):
                update_match_video_url(
                    session_id=session_id,
                    video_url=video_url,
                    video_title=youtube_title,
                )
                session["video_title"] = youtube_title
        elif event.get("action") == "start":
            st.session_state[pending_start_key] = int(event.get("seconds") or 0)
            st.session_state.pop(pending_end_key, None)
            st.session_state[resume_token_key] += 1
            st.rerun(scope="app")
        elif event.get("action") == "end" and pending_start_key in st.session_state:
            st.session_state[pending_end_key] = int(event.get("seconds") or 0)
        elif event.get("action") in {"timeout", "substitution"} and pending_start_key not in st.session_state:
            st.session_state[marker_action_key] = str(event["action"])
            st.session_state[marker_seconds_key] = int(event.get("seconds") or 0)

    pending_start = st.session_state.get(pending_start_key)
    pending_end = st.session_state.get(pending_end_key)
    marker_action = st.session_state.get(marker_action_key)
    marker_seconds = st.session_state.get(marker_seconds_key)
    if marker_action and marker_seconds is not None:
        _render_video_marker_form(
            session,
            roster,
            cut_state,
            marker_action=str(marker_action),
            marker_seconds=int(marker_seconds),
            action_key=marker_action_key,
            seconds_key=marker_seconds_key,
            resume_token_key=resume_token_key,
        )
    if pending_start is not None and pending_end is None:
        st.info(f"Punkt läuft ab {format_video_timestamp(int(pending_start))}.")
    if pending_start is not None and pending_end is not None:
        st.markdown(f"#### Wer gewinnt den Punkt bei {format_video_timestamp(int(pending_end))}?")
        us_button, opponent_button = st.columns(2)
        winner = None
        if us_button.button(
            f"Punkt {TEAM_NAME}",
            type="primary",
            width="stretch",
            key=f"video_cut_us_{session_id}_{cut_state['current_set']}_{cut_state['rally_number']}",
        ):
            winner = "us"
        if opponent_button.button(
            f"Punkt {session['opponent']}",
            width="stretch",
            key=f"video_cut_opponent_{session_id}_{cut_state['current_set']}_{cut_state['rally_number']}",
        ):
            winner = "opponent"
        if winner:
            target_set = int(cut_state["current_set"])
            target_rally = int(cut_state["rally_number"])
            updated_cut_state = award_video_cut_point(cut_state, winner)
            score_record = updated_cut_state["history"][-1]
            save_match_video_segment(
                session_id=session_id,
                set_number=target_set,
                rally_number=target_rally,
                start_seconds=int(pending_start),
                end_seconds=int(pending_end),
                winner=winner,
                our_score=int(score_record["our_score"]),
                opponent_score=int(score_record["opponent_score"]),
            )
            updated_state = deepcopy(session["state"])
            updated_state["video_cut_state"] = updated_cut_state
            _save_state(session_id, updated_state)
            st.session_state.pop(pending_start_key, None)
            st.session_state.pop(pending_end_key, None)
            st.session_state[seek_seconds_key] = int(pending_end)
            st.session_state[resume_token_key] += 1
            st.rerun(scope="app")

    if segments:
        undo_column, count_column = st.columns([1, 2])
        count_column.metric("Geschnittene Punkte", len(segments))
        if undo_column.button(
            "↩ Letzten Schnitt zurück",
            width="stretch",
            key=f"undo_video_cut_{session_id}",
        ):
            try:
                restored_cut_state, removed = undo_video_cut_point(cut_state)
            except ValueError as error:
                st.error(str(error))
            else:
                removed_segment = next(
                    (
                        segment
                        for segment in reversed(segments)
                        if int(segment["set_number"]) == int(removed["set_number"])
                        and int(segment["rally_number"]) == int(removed["rally_number"])
                    ),
                    None,
                )
                if removed_segment:
                    delete_match_video_segment(
                        segment_id=int(removed_segment["id"]),
                        session_id=session_id,
                    )
                    st.session_state[seek_seconds_key] = int(removed_segment["start_seconds"])
                    st.session_state[seek_token_key] += 1
                updated_state = deepcopy(session["state"])
                updated_state["video_cut_state"] = restored_cut_state
                _save_state(session_id, updated_state)
                st.session_state.pop(pending_start_key, None)
                st.session_state.pop(pending_end_key, None)
                st.rerun(scope="app")
        with st.expander(f"Gespeicherte Punkte ({len(segments)})"):
            st.dataframe(
                [
                    {
                        "Punkt": _video_point_label(
                            (int(segment["set_number"]), int(segment["rally_number"]))
                        ),
                        "Stand": f"{segment['our_score']}:{segment['opponent_score']}",
                        "Gewinner": TEAM_NAME if segment.get("winner") == "us" else session["opponent"],
                        "Start": format_video_timestamp(int(segment["start_seconds"])),
                        "Ende": format_video_timestamp(int(segment["end_seconds"])),
                    }
                    for segment in segments
                ],
                width="stretch",
                hide_index=True,
            )

    if video_events:
        delete_column, count_column = st.columns([1, 2])
        count_column.metric("Timeouts und Wechsel", len(video_events))
        if delete_column.button(
            "↩ Letzte Markierung löschen",
            width="stretch",
            key=f"delete_last_video_marker_{session_id}",
        ):
            delete_match_video_event(
                event_id=int(max(video_events, key=lambda item: int(item["id"]))["id"]),
                session_id=session_id,
            )
            st.rerun(scope="app")
        with st.expander(f"Timeouts und Wechsel ({len(video_events)})"):
            st.dataframe(
                _video_event_rows(session, video_events),
                width="stretch",
                hide_index=True,
            )


def _render_analysis_video_clip(session: dict[str, Any]) -> None:
    state = session.get("state", {})
    if "analysis_video_session_id" in state:
        source_session_id = state.get("analysis_video_session_id")
    else:
        source_session_id = session.get("id") if session.get("video_url") else None
    if source_session_id is None:
        return
    source_session = get_match_session(int(source_session_id))
    if not source_session or not source_session.get("video_url"):
        st.info("Das ausgewählte Video ist nicht mehr verfügbar. Die Analyse geht ohne Video weiter.")
        return
    video_url = source_session["video_url"]
    target = (int(state.get("current_set") or 1), int(state.get("rally_number") or 1))
    segment = next(
        (
            item
            for item in list_match_video_segments(session_id=int(source_session_id))
            if (int(item["set_number"]), int(item["rally_number"])) == target
        ),
        None,
    )
    if not segment:
        st.info(
            f"Für {_video_point_label(target)} gibt es keinen vorbereiteten Videoschnitt. "
            "Du kannst trotzdem ohne Video weiteranalysieren."
        )
        return
    winner = TEAM_NAME if segment.get("winner") == "us" else source_session["opponent"]
    video_title = source_session.get("video_title") or _video_fallback_title(video_url)
    with st.expander(
        f"🎬 {video_title} · {_video_point_label(target)} · {winner}",
        expanded=True,
    ):
        st.video(
            video_url,
            start_time=int(segment["start_seconds"]),
            end_time=int(segment["end_seconds"]),
        )
        st.caption(
            f"Vorbereiteter Spielstand nach diesem Punkt: {segment['our_score']}:{segment['opponent_score']}"
        )
        events_before_rally = [
            item
            for item in list_match_video_events(session_id=int(source_session_id))
            if int(item["set_number"]) == target[0] and int(item["rally_number"]) == target[1]
        ]
        if events_before_rally:
            event_texts = [row["Ereignis"] for row in _video_event_rows(source_session, events_before_rally)]
            st.caption("Vor diesem Punkt: " + " · ".join(event_texts))


def _undo_last_rally_and_refresh(session: dict[str, Any]) -> None:
    try:
        restored_state, removed_rally = undo_last_point(session["state"])
    except ValueError as error:
        st.error(f"Der letzte Punkt kann nicht zurückgenommen werden: {error}")
        return
    delete_match_rally_actions(
        session_id=session["id"],
        set_number=int(removed_rally["set_number"]),
        rally_number=int(removed_rally["rally_number"]),
    )
    _save_state(session["id"], restored_state)
    st.rerun(scope="app")


def _award_and_refresh(
    session: dict[str, Any],
    winner: str,
    reason: str,
    *,
    result_kind: str = "point",
) -> None:
    _save_state(
        session["id"],
        award_point(session["state"], winner, reason, result_kind=result_kind),
    )
    st.rerun(scope="app")


def _finish_our_attack(
    session: dict[str, Any],
    state: dict[str, Any],
    *,
    attacker_name: str,
    attack_result: str,
    attack_block_outcome: str,
) -> None:
    if attack_block_outcome == "blockout":
        _award_and_refresh(session, "us", f"Blockout {attacker_name}")
    elif attack_block_outcome == "blocked_point":
        _award_and_refresh(
            session,
            "opponent",
            f"Geblockt {attacker_name}",
            result_kind="our_error",
        )
    elif attack_block_outcome == "recycle_us":
        _save_state(session["id"], recycle_block_to_us(state))
        st.rerun(scope="app")
    elif attack_block_outcome == "touch_opponent":
        _save_state(session["id"], continue_to_opponent(state))
        st.rerun(scope="app")
    elif attack_result == "point":
        _award_and_refresh(session, "us", f"Angriffspunkt {attacker_name}")
    elif attack_result == "error":
        _award_and_refresh(
            session,
            "opponent",
            f"Angriffsfehler {attacker_name}",
            result_kind="our_error",
        )
    else:
        _save_state(session["id"], continue_to_opponent(state))
        st.rerun(scope="app")


COURT_POSITION_ORDER = (4, 3, 2, 5, 6, 1)


def _position_defaults(
    lineup: list[Any],
    rotation: int,
    lineup_roles: dict[str, Any] | None = None,
) -> dict[str, str]:
    if lineup_roles:
        return rotation_slots_for_lineup(rotation, lineup_roles)
    setter = _default_player(lineup, ("setter", "libero", "outside", "opposite", "middle"))
    setter_position = int(rotation)
    positions = {str(setter_position): setter.id}
    remaining_players = [player for player in lineup if player.id != setter.id]
    remaining_positions = [position for position in COURT_POSITION_ORDER if position != setter_position]
    positions.update(
        {str(position): player.id for position, player in zip(remaining_positions, remaining_players)}
    )
    return positions


DEMO_MATCH_METADATA_KEY = "example_match_v17_session_id"
LEGACY_DEMO_MATCH_METADATA_KEYS = (
    "example_match_v1_session_id",
    "example_match_v2_session_id",
    "example_match_v3_session_id",
    "example_match_v4_session_id",
    "example_match_v5_session_id",
    "example_match_v6_session_id",
    "example_match_v7_session_id",
    "example_match_v8_session_id",
    "example_match_v9_session_id",
    "example_match_v10_session_id",
    "example_match_v11_session_id",
    "example_match_v12_session_id",
    "example_match_v13_session_id",
    "example_match_v14_session_id",
    "example_match_v15_session_id",
    "example_match_v16_session_id",
)
DEMO_MATCH_OPPONENT = "Beispielmatch"


def _is_example_match_name(value: Any) -> bool:
    name = str(value or "").strip().casefold()
    return name == DEMO_MATCH_OPPONENT.casefold() or name.startswith("beispiel ·")


def _demo_storage_kwargs(db_path: Any | None) -> dict[str, Any]:
    return {} if db_path is None else {"db_path": db_path}


def _demo_match_state(lineup: list[Any], lineup_roles: dict[str, Any]) -> dict[str, Any]:
    set_scores = ((25, 20), (22, 25), (25, 19), (25, 21))
    starting_rotations = (1, 6, 5, 4)
    first_servers = ("opponent", "us", "opponent", "us")
    positions = _position_defaults(lineup, starting_rotations[0], lineup_roles)
    state = new_match_state(
        first_servers[0],
        starting_rotation=starting_rotations[0],
        positions=positions,
        lineup_roles=lineup_roles,
    )

    for set_index, (our_target, opponent_target) in enumerate(set_scores):
        winners: list[str] = []
        for rally_index in range(min(our_target, opponent_target)):
            pair = ["opponent", "us"] if rally_index % 2 == 0 else ["us", "opponent"]
            winners.extend(pair)
        if our_target > opponent_target:
            winners.extend(["us"] * (our_target - opponent_target))
        else:
            winners.extend(["opponent"] * (opponent_target - our_target))

        for rally_index, winner in enumerate(winners, start=1):
            if winner == "us":
                reasons = ("Angriffspunkt", "Blockpunkt", "Gegnerfehler", "Servicepunkt")
                reason = reasons[rally_index % len(reasons)]
                result_kind = "opponent_error" if reason == "Gegnerfehler" else "point"
            else:
                own_error = rally_index % 3 == 0
                reason = "Eigener Fehler" if own_error else "Gegnerischer Angriff"
                result_kind = "our_error" if own_error else "opponent_point"
            state = award_point(state, winner, reason, result_kind=result_kind)

        if set_index < len(set_scores) - 1:
            next_rotation = starting_rotations[set_index + 1]
            state = start_next_set(
                state,
                first_servers[set_index + 1],
                starting_rotation=next_rotation,
                positions=_position_defaults(lineup, next_rotation, lineup_roles),
            )
    return state


def _save_demo_match_actions(
    session_id: int,
    lineup: list[Any],
    lineup_roles: dict[str, Any],
    state: dict[str, Any],
    *,
    db_path: Any | None = None,
) -> None:
    """Save a deterministic, match-like action sample for every completed rally."""

    storage_kwargs = _demo_storage_kwargs(db_path)
    players = _players_by_id(lineup)
    player_order = {player.id: index for index, player in enumerate(lineup)}
    receiver_cycle = (
        lineup_roles["libero"],
        lineup_roles["outsides"][0],
        lineup_roles["libero"],
        lineup_roles["outsides"][1],
        lineup_roles["libero"],
        lineup_roles["outsides"][0],
        lineup_roles["outsides"][1],
    )
    quality_patterns = {
        "serve_receive": (
            "good",
            "perfect",
            "okay",
            "good",
            "bad",
            "perfect",
            "good",
            "okay",
            "good",
            "error",
            "perfect",
            "good",
        ),
        "attack_defense": (
            "okay",
            "good",
            "bad",
            "okay",
            "perfect",
            "good",
            "okay",
            "bad",
            "good",
            "error",
        ),
        "freeball": ("perfect", "good", "perfect", "good", "okay", "perfect"),
    }
    first_contact_cells = {
        quality: tuple(
            (cell_x, cell_y)
            for cell_y, row in enumerate(FIRST_CONTACT_QUALITY_ZONES)
            for cell_x, cell_quality in enumerate(row)
            if cell_quality == quality
        )
        for quality in ("perfect", "good", "okay", "bad")
    }
    attack_cells = {
        "spike": (
            (0, 8),
            (2, 7),
            (8, 8),
            (6, 6),
            (4, 8),
            (1, 5),
            (7, 5),
            (3, 6),
            (8, 4),
            (0, 6),
            (5, 7),
            (2, 4),
            (6, 8),
            (4, 5),
        ),
        "tip": (
            (2, 2),
            (5, 2),
            (3, 3),
            (7, 2),
            (1, 3),
            (6, 4),
            (4, 1),
            (8, 3),
            (3, 4),
        ),
        "safe": (
            (4, 8),
            (6, 7),
            (2, 8),
            (7, 8),
            (3, 6),
            (5, 8),
            (1, 7),
            (8, 6),
        ),
    }
    out_cells = (
        (-1, 7),
        (9, 6),
        (3, 9),
        (7, 9),
        (-1, 4),
        (9, 3),
        (5, 9),
        (1, 9),
    )
    opponent_origins = ("outside", "outside", "opposite", "middle", "outside", "opposite")
    contact_number = 0
    attack_number = 0
    block_number = 0

    for rally in state.get("rally_history", []):
        set_number = int(rally.get("set_number") or 1)
        rally_number = int(rally.get("rally_number") or 1)
        rotation = int(rally.get("rotation") or 1)
        winner = str(rally.get("winner") or "opponent")
        serving_team = str(rally.get("serving_team_before") or "opponent")
        slots = dict(rally.get("rotation_slots_before") or rotation_slots_for_lineup(rotation, lineup_roles))
        common = {
            "session_id": session_id,
            "rally_number": rally_number,
            "match_date": "2026-08-10",
            "opponent": DEMO_MATCH_OPPONENT,
            "set_number": set_number,
            **storage_kwargs,
        }
        sequence_number = 1
        rally_key = set_number * 53 + rally_number * 7

        if serving_team == "us":
            server_id = slots.get("1") or lineup_roles["setter"]
            if winner == "us" and rally_key % 9 == 0:
                service_result = "ace"
            elif winner == "opponent" and rally_key % 11 == 0:
                service_result = "error"
            else:
                service_result = "in_play"
            save_match_action(
                **common,
                sequence_number=sequence_number,
                ball_type="service",
                receiver_id="",
                receiver_name="",
                first_contact_quality="",
                setter_involved=False,
                server_id=server_id,
                server_name=players[server_id].name,
                service_type="standing" if rally_key % 3 == 0 else "jump",
                service_result=service_result,
                service_origin_x=(rally_key + rotation) % 9,
                service_origin_y=0,
                landing_x=(
                    (-1 if rally_key % 2 else 9)
                    if service_result == "error"
                    else (rally_key * 3 + set_number) % 9
                ),
                landing_y=(rally_key * 5 + rotation) % 9,
                landing_out=service_result == "error",
            )
            sequence_number += 1
            if service_result in {"ace", "error"}:
                continue

            ball_type = "freeball" if rally_key % 9 == 0 else "attack_defense"
            if rally_key % 4 == 0:
                block_number += 1
                front_ids = [slots.get(position, "") for position in ("2", "3", "4")]
                front_middle = next(
                    (player_id for player_id in lineup_roles["middles"] if player_id in front_ids),
                    lineup_roles["middles"][0],
                )
                front_outside = next(
                    (player_id for player_id in lineup_roles["outsides"] if player_id in front_ids),
                    lineup_roles["outsides"][0],
                )
                right_blocker = next(
                    (
                        player_id
                        for player_id in (lineup_roles["setter"], lineup_roles["opposite"])
                        if player_id in front_ids
                    ),
                    front_middle,
                )
                opponent_origin = opponent_origins[(block_number + set_number) % len(opponent_origins)]
                blocker_candidates = {
                    "outside": (front_middle, right_blocker),
                    "middle": (front_middle,),
                    "opposite": (front_middle, front_outside),
                }[opponent_origin]
                block_pattern = block_number % 10
                if block_pattern == 0:
                    block_result = "no_touch"
                    block_formation = "not_needed"
                    blocker_id = ""
                elif winner == "us" and block_pattern in {1, 5}:
                    block_result = "point"
                    block_formation = "closed"
                    blocker_id = blocker_candidates[block_number % len(blocker_candidates)]
                elif winner == "opponent" and block_pattern in {3, 8}:
                    block_result = "error"
                    block_formation = "middle_late"
                    blocker_id = front_middle
                else:
                    block_result = "touch"
                    block_formation = "middle_late" if block_pattern == 7 else "closed"
                    blocker_id = blocker_candidates[block_number % len(blocker_candidates)]
                save_match_action(
                    **common,
                    sequence_number=sequence_number,
                    ball_type="block",
                    receiver_id="",
                    receiver_name="",
                    first_contact_quality="",
                    setter_involved=False,
                    opponent_attack_origin=opponent_origin,
                    block_player_id=blocker_id,
                    block_player_name=players[blocker_id].name if blocker_id else "",
                    block_result=block_result,
                    block_formation=block_formation,
                )
                sequence_number += 1
                if block_result in {"point", "error"}:
                    continue
                ball_type = "freeball" if block_result == "no_touch" else "attack_defense"
        else:
            if winner == "us" and rally_key % 13 == 0:
                # Opponent service error: the point has no reception action.
                continue
            ball_type = "serve_receive"

        contact_number += 1
        receiver_id = receiver_cycle[(contact_number * 2 + set_number + rotation) % len(receiver_cycle)]
        quality_pattern = quality_patterns[ball_type]
        quality = quality_pattern[(contact_number + set_number * 3 + rally_number) % len(quality_pattern)]
        if quality == "error" and winner != "opponent":
            quality = "okay"

        if quality == "error":
            save_match_action(
                **common,
                sequence_number=sequence_number,
                ball_type=ball_type,
                receiver_id=receiver_id,
                receiver_name=players[receiver_id].name,
                first_contact_quality="error",
                setter_involved=False,
                opponent_attack_origin=(
                    opponent_origins[contact_number % len(opponent_origins)]
                    if ball_type == "attack_defense"
                    else ""
                ),
            )
            continue

        cell_pool = first_contact_cells[quality]
        cell_x, cell_y = cell_pool[(contact_number * 5 + rally_number * 3 + set_number * 7) % len(cell_pool)]
        first_contact_too_low = quality in {"okay", "bad"} and (contact_number + set_number) % 5 == 0
        quality = suggested_first_contact_quality(
            cell_x,
            cell_y,
            too_low=first_contact_too_low,
        )
        set_origin_x, set_origin_y = group_set_origin_2x2(cell_x + 0.5, cell_y + 0.5)
        front_ids = [slots.get(position, "") for position in ("2", "3", "4")]
        front_outside = next(
            (player_id for player_id in lineup_roles["outsides"] if player_id in front_ids),
            lineup_roles["outsides"][0],
        )
        front_middle = next(
            (player_id for player_id in lineup_roles["middles"] if player_id in front_ids),
            lineup_roles["middles"][0],
        )
        is_l1_sideout = ball_type == "serve_receive" and rotation == 1
        outside_origin = "2" if is_l1_sideout else "4"
        opposite_origin = "4" if is_l1_sideout else ("2" if lineup_roles["opposite"] in front_ids else "1")
        attacker_choices = [
            (front_outside, outside_origin),
            (lineup_roles["opposite"], opposite_origin),
            (front_outside, outside_origin),
            (lineup_roles["opposite"], opposite_origin),
        ]
        if quality in {"perfect", "good"}:
            attacker_choices.extend(
                [
                    (front_middle, "3"),
                    (front_outside, outside_origin),
                    (front_middle, "3"),
                ]
            )
        attacker_id, attack_origin = attacker_choices[
            (contact_number + rally_number + set_number) % len(attacker_choices)
        ]

        setter_id = lineup_roles["setter"]
        setter_is_front = setter_id in front_ids
        if (
            setter_is_front
            and quality in {"perfect", "good"}
            and (contact_number + rally_number + set_number) % 17 == 0
        ):
            attacker_id = setter_id
            attack_origin = "2"
            attack_type = "setter_tip"
        elif ball_type == "freeball" and (contact_number + rally_number) % 3 == 0:
            attacker_id = lineup_roles["libero"]
            attack_origin = "5"
            attack_type = "safe"
        elif attacker_id in lineup_roles["middles"]:
            attack_type = "spike"
        elif quality in {"perfect", "good"}:
            attack_type = ("spike", "spike", "tip", "spike", "safe", "spike")[
                (contact_number + set_number) % 6
            ]
        else:
            attack_type = ("safe", "spike", "tip", "spike", "spike")[(contact_number + set_number) % 5]

        attack_number += 1
        if attack_type == "setter_tip":
            set_tendency_values: tuple[str, ...] = ()
            set_quality = ""
        else:
            if attacker_id in lineup_roles["middles"]:
                tendency_pool = (
                    ("optimal",),
                    ("optimal",),
                    ("too_high",),
                    ("too_low",),
                )
            elif quality == "perfect":
                tendency_pool = (
                    ("optimal",),
                    ("optimal",),
                    ("too_high",),
                    ("too_far_inside",),
                    ("too_far_outside",),
                )
            elif quality == "good":
                tendency_pool = (
                    ("optimal",),
                    ("too_high",),
                    ("too_far_inside",),
                    ("too_far_outside", "too_close_net"),
                    ("too_low", "too_far_net"),
                )
            elif quality == "okay":
                tendency_pool = (
                    ("too_high",),
                    ("too_far_inside",),
                    ("too_far_outside",),
                    ("too_high", "too_close_net"),
                    ("too_far_inside", "too_far_net"),
                )
            else:
                tendency_pool = (
                    ("too_high", "too_far_net"),
                    ("too_far_inside", "too_low"),
                    ("too_far_outside", "too_far_net"),
                )
            set_tendency_values = tendency_pool[(attack_number + set_number + rotation) % len(tendency_pool)]
            set_quality_pool = {
                "perfect": ("very_good", "very_good", "good"),
                "good": ("very_good", "good", "good", "okay"),
                "okay": ("okay", "playable", "good"),
                "bad": ("playable", "bad", "okay"),
            }[quality]
            set_quality = set_quality_pool[(attack_number + rally_number) % len(set_quality_pool)]

        if "too_far_inside" in set_tendency_values:
            set_inside_meters = (
                (0.5, 1.0)[attack_number % 2]
                if quality in {"perfect", "good"}
                else (1.5, 2.0)[attack_number % 2]
            )
        else:
            set_inside_meters = 0.0

        outcome_key = attack_number + set_number * 7 + rally_number
        attack_block_outcome = "none"
        if attack_type in {"spike", "tip"}:
            if winner == "us" and outcome_key % 10 == 0:
                attack_block_outcome = "blockout"
            elif winner == "opponent" and outcome_key % 12 == 0:
                attack_block_outcome = "blocked_point"
            elif outcome_key % 15 == 0:
                attack_block_outcome = "recycle_us"
            elif outcome_key % 7 == 0:
                attack_block_outcome = "touch_opponent"

        if winner == "us":
            if attack_type == "safe":
                normal_result = "point" if outcome_key % 8 == 0 else "continued"
            elif attack_type in {"tip", "setter_tip"}:
                normal_result = "point" if outcome_key % 3 else "continued"
            else:
                normal_result = "continued" if outcome_key % 4 == 0 else "point"
        else:
            normal_result = "error" if outcome_key % 3 == 0 else "continued"
        attack_result = attack_result_for_block_outcome(
            attack_block_outcome,
            normal_result,
        )

        if attack_block_outcome in {"blockout", "blocked_point", "recycle_us"}:
            landing_x = None
            landing_y = None
        elif attack_result == "error":
            landing_x, landing_y = out_cells[
                (attack_number * 3 + set_number + player_order[attacker_id]) % len(out_cells)
            ]
        else:
            landing_kind = attack_type if attack_type in attack_cells else "tip"
            landing_pool = attack_cells[landing_kind]
            landing_x, landing_y = landing_pool[
                (attack_number * 5 + set_number * 3 + player_order[attacker_id] * 2) % len(landing_pool)
            ]

        save_match_action(
            **common,
            sequence_number=sequence_number,
            ball_type=ball_type,
            receiver_id=receiver_id,
            receiver_name=players[receiver_id].name,
            first_contact_quality=quality,
            first_contact_x=cell_x,
            first_contact_y=cell_y,
            first_contact_too_low=first_contact_too_low,
            setter_involved=True,
            setter_id=setter_id,
            setter_name=players[setter_id].name,
            setter_movement=(
                "late" if quality in {"okay", "bad"} and (contact_number + rotation) % 4 == 0 else "fast"
            ),
            set_quality=set_quality,
            set_tendency=",".join(set_tendency_values),
            set_inside_meters=set_inside_meters,
            set_origin="reception_target",
            set_origin_x=set_origin_x,
            set_origin_y=set_origin_y,
            attacker_id=attacker_id,
            attacker_name=players[attacker_id].name,
            attack_type=attack_type,
            attack_result=attack_result,
            attack_block_outcome=attack_block_outcome,
            attack_origin=attack_origin,
            landing_x=landing_x,
            landing_y=landing_y,
            landing_out=(
                landing_cell_is_out(landing_x, landing_y)
                if landing_x is not None and landing_y is not None
                else False
            ),
            opponent_attack_origin=(
                opponent_origins[contact_number % len(opponent_origins)]
                if ball_type == "attack_defense"
                else ""
            ),
        )


def ensure_demo_match(roster: tuple[Any, ...], *, db_path: Any | None = None) -> int | None:
    """Create one completed example match per database so the analysis is immediately visible."""
    storage_kwargs = _demo_storage_kwargs(db_path)
    existing = get_app_metadata(DEMO_MATCH_METADATA_KEY, **storage_kwargs)
    if existing is not None:
        try:
            existing_session_id = int(existing)
        except (TypeError, ValueError):
            existing_session_id = 0
        existing_session = (
            get_match_session(existing_session_id, **storage_kwargs) if existing_session_id else None
        )
        if (
            existing_session is not None
            and _is_example_match_name(existing_session.get("opponent"))
            and list_match_actions(
                session_id=existing_session_id,
                **storage_kwargs,
            )
        ):
            return None
        if existing_session is not None and _is_example_match_name(existing_session.get("opponent")):
            delete_match_session(
                session_id=existing_session_id,
                **storage_kwargs,
            )

    for legacy_key in LEGACY_DEMO_MATCH_METADATA_KEYS:
        legacy_session_id = get_app_metadata(legacy_key, **storage_kwargs)
        if legacy_session_id:
            delete_match_session(session_id=int(legacy_session_id), **storage_kwargs)

    lineup = _default_lineup(roster)
    lineup_roles = assign_lineup_roles(lineup)
    if len(lineup) != 7 or lineup_roles is None:
        raise ValueError("Der Beispielmatch braucht einen gültigen 7er-Kader.")

    state = _demo_match_state(lineup, lineup_roles)
    session_id = create_match_session(
        match_date="2026-08-10",
        opponent=DEMO_MATCH_OPPONENT,
        lineup_player_ids=[player.id for player in lineup],
        state=state,
        **storage_kwargs,
    )
    try:
        _save_demo_match_actions(
            session_id,
            lineup,
            lineup_roles,
            state,
            db_path=db_path,
        )
    except Exception:
        delete_match_session(session_id=session_id, **storage_kwargs)
        raise
    set_app_metadata(DEMO_MATCH_METADATA_KEY, str(session_id), **storage_kwargs)
    return session_id


def _render_position_assignment(
    lineup: list[Any],
    rotation: int,
    *,
    key_prefix: str,
    lineup_roles: dict[str, Any] | None = None,
) -> dict[str, str]:
    player_map = _players_by_id(lineup)
    defaults = _position_defaults(lineup, rotation, lineup_roles)
    signature = "_".join(sorted(player_map))
    st.caption(
        f"L{rotation} = Zuspieler auf Position {rotation} · "
        "Netz vorne 4–3–2, hinten 5–6–1 · Libero wird automatisch eingesetzt"
    )
    positions: dict[str, str] = {}
    for row_positions in ((4, 3, 2), (5, 6, 1)):
        columns = st.columns(3)
        for column, position in zip(columns, row_positions):
            default_id = defaults[str(position)]
            options = list(player_map)
            positions[str(position)] = column.selectbox(
                f"Position {position}",
                options=options,
                index=options.index(default_id),
                format_func=lambda player_id: player_map[player_id].name,
                key=f"{key_prefix}_{signature}_{rotation}_{position}",
            )
    return positions


def _session_analysis_ready(session: dict[str, Any]) -> bool:
    state = session.get("state", {})
    return bool(
        len(session.get("lineup_player_ids", [])) == 7
        and state.get("lineup_roles")
        and len(state.get("rotation_slots", {})) == 6
    )


def _render_video_project_setup() -> None:
    open_sessions = list_match_sessions(active_only=True)
    if open_sessions:
        st.markdown("### Vorhandenes Match oder Video öffnen")
        resume_session = st.selectbox(
            "Vorhandenes Match",
            options=open_sessions,
            format_func=_session_label,
            key="resume_video_session",
            label_visibility="collapsed",
        )
        if st.button("Öffnen", width="stretch", key="resume_video_button"):
            st.session_state.live_match_session_id = resume_session["id"]
            st.rerun()
        st.markdown("---")

    st.markdown("### Neues Video schneiden")
    st.caption("Für diesen Schritt brauchst du noch keinen Kader und keine Startaufstellung.")
    setup_date, setup_opponent = st.columns([1, 2])
    match_date = setup_date.date_input("Datum", value=date.today(), key="new_video_date")
    opponent = setup_opponent.text_input(
        "Gegner",
        placeholder="z. B. VBC Beispiel",
        key="new_video_opponent",
    )
    video_url_input = st.text_input(
        "YouTube-Link",
        placeholder="https://www.youtube.com/watch?v=…",
        key="new_video_project_url",
    )
    video_title_input = st.text_input(
        "Videotitel (optional)",
        placeholder="Wird beim Laden automatisch aus YouTube übernommen",
        key="new_video_project_title",
    )
    if st.button("Video öffnen und schneiden", type="primary", width="stretch"):
        try:
            video_url = normalize_youtube_url(video_url_input)
        except ValueError as error:
            st.error(str(error))
            return
        if not opponent.strip():
            st.error("Bitte den Gegner eintragen.")
        elif not video_url:
            st.error("Bitte den YouTube-Link einfügen.")
        else:
            initial_state = new_match_state("opponent")
            initial_state["video_cut_state"] = new_video_cut_state()
            session_id = create_match_session(
                match_date=match_date.isoformat(),
                opponent=opponent,
                lineup_player_ids=[],
                video_url=video_url,
                video_title=video_title_input.strip() or _video_fallback_title(video_url),
                state=initial_state,
            )
            st.session_state.live_match_session_id = session_id
            st.rerun()


def _render_existing_video_analysis_setup(
    session: dict[str, Any],
    roster: tuple[Any, ...],
    player_label: Callable[[Any], str],
) -> None:
    session_id = int(session["id"])
    st.markdown("### Matchanalyse vorbereiten")
    video_sources = _video_source_sessions()
    source_map = {int(source["id"]): source for source in video_sources}
    selected_source_id = st.selectbox(
        "Geschnittenes Video für diese Analyse (optional)",
        options=[None, *source_map],
        index=0,
        format_func=lambda source_id: _video_source_label(source_map.get(source_id)),
        key=f"video_analysis_source_{session_id}",
    )
    selected_source = source_map.get(selected_source_id)
    segments = (
        list_match_video_segments(session_id=int(selected_source_id))
        if selected_source_id is not None
        else []
    )
    if selected_source:
        st.caption(
            f"Ausgewählt: {_video_source_label(selected_source)}. "
            "Jetzt legst du Kader, Aufschlagrecht und Startläufer fest."
        )
    else:
        st.caption("Die Analyse wird ohne Video vorbereitet.")
    player_map = _players_by_id(roster)
    default_lineup, preferred_lineup_roles = _match_setup_lineup_defaults(roster)
    lineup_ids = st.pills(
        "Welche 7 Spieler gehören zum Matchkader?",
        options=list(player_map),
        default=[player.id for player in default_lineup],
        format_func=lambda player_id: player_map[player_id].name,
        selection_mode="multi",
        key=f"video_analysis_lineup_{session_id}",
    )
    lineup = [player_map[player_id] for player_id in lineup_ids or []]
    lineup_roles = assign_lineup_roles(lineup, preferred_roles=preferred_lineup_roles)
    system_book_note = (
        " · Systembuch-Besetzung übernommen"
        if preferred_lineup_roles is not None and lineup_roles == preferred_lineup_roles
        else ""
    )
    st.caption(f"{len(lineup)}/7 Spieler ausgewählt{system_book_note}")
    if len(lineup) == 7 and lineup_roles is None:
        st.error("Die Auswahl braucht 1 Zuspieler, 1 Dia, 2 Aussen, 2 Mitten und 1 Libero.")
    first_server = st.segmented_control(
        "Wer hat im ersten Satz zuerst Service?",
        options=["us", "opponent"],
        default="opponent",
        format_func=lambda value: "Wir haben Service" if value == "us" else "Gegner hat Service",
        key=f"video_analysis_first_server_{session_id}",
    )
    starting_rotation = st.segmented_control(
        "In welchem Läufer starten wir?",
        options=[1, 2, 3, 4, 5, 6],
        default=1,
        format_func=lambda value: f"L{value}",
        key=f"video_analysis_rotation_{session_id}",
    )
    positions: dict[str, str] = {}
    if len(lineup) == 7 and lineup_roles:
        rotational_ids = {
            lineup_roles["setter"],
            lineup_roles["opposite"],
            *lineup_roles["outsides"],
            *lineup_roles["middles"],
        }
        rotational_players = [player for player in lineup if player.id in rotational_ids]
        st.markdown("#### Startpositionen")
        st.caption(
            f"{player_map[lineup_roles['libero']].name} ist der Libero und ersetzt automatisch "
            "die hintere Mitte."
        )
        positions = _render_position_assignment(
            rotational_players,
            starting_rotation,
            key_prefix=f"video_analysis_position_{session_id}",
            lineup_roles=lineup_roles,
        )
    if st.button(
        "Detailanalyse starten",
        type="primary",
        width="stretch",
        key=f"configure_video_analysis_{session_id}",
    ):
        if len(lineup) != 7 or lineup_roles is None:
            st.error("Bitte genau 7 Spieler in der geforderten Rollenverteilung auswählen.")
        elif len(set(positions.values())) != 6:
            st.error("Jeder Spieler darf nur auf einer Startposition stehen.")
        elif positions.get(str(starting_rotation)) != lineup_roles["setter"]:
            st.error(f"Bei L{starting_rotation} muss der Zuspieler auf Position {starting_rotation} stehen.")
        else:
            analysis_state = new_match_state(
                first_server,
                starting_rotation=starting_rotation,
                positions=positions,
                lineup_roles=lineup_roles,
            )
            analysis_state["video_cut_state"] = _video_cut_state(session)
            analysis_state["analysis_video_session_id"] = selected_source_id
            _save_state(
                session_id,
                analysis_state,
                lineup_player_ids=[player.id for player in lineup],
            )
            st.rerun()


def _render_match_setup(roster: tuple[Any, ...], player_label: Callable[[Any], str]) -> None:
    open_sessions = list_match_sessions(active_only=True)
    if open_sessions:
        st.markdown("### Offenes Match fortsetzen")
        resume_session = st.selectbox(
            "Offenes Match",
            options=open_sessions,
            format_func=_session_label,
            key="resume_match_session",
            label_visibility="collapsed",
        )
        if st.button("Match fortsetzen", use_container_width=True, key="resume_match_button"):
            st.session_state.live_match_session_id = resume_session["id"]
            st.rerun()
        st.markdown("---")

    st.markdown("### Neues Match starten")
    setup_date, setup_opponent = st.columns([1, 2])
    match_date = setup_date.date_input("Datum", value=date.today(), key="new_match_date")
    opponent = setup_opponent.text_input("Gegner", placeholder="z. B. VBC Beispiel", key="new_match_opponent")
    video_sources = _video_source_sessions()
    source_map = {int(source["id"]): source for source in video_sources}
    selected_source_id = st.selectbox(
        "Geschnittenes Video (optional)",
        options=[None, *source_map],
        format_func=lambda source_id: _video_source_label(source_map.get(source_id)),
        key="new_match_video_source",
        help="Du kannst bewusst ohne Video analysieren oder ein zuvor geschnittenes Video auswählen.",
    )
    player_map = _players_by_id(roster)
    default_lineup, preferred_lineup_roles = _match_setup_lineup_defaults(roster)
    lineup_ids = st.pills(
        "Welche 7 Spieler gehören zum Matchkader?",
        options=list(player_map),
        default=[player.id for player in default_lineup],
        format_func=lambda player_id: player_map[player_id].name,
        selection_mode="multi",
        key="new_match_lineup",
    )
    lineup = [player_map[player_id] for player_id in lineup_ids or []]
    lineup_roles = assign_lineup_roles(lineup, preferred_roles=preferred_lineup_roles)
    system_book_note = (
        " · Systembuch-Besetzung übernommen"
        if preferred_lineup_roles is not None and lineup_roles == preferred_lineup_roles
        else ""
    )
    st.caption(f"{len(lineup)}/7 Spieler ausgewählt{system_book_note}")
    if len(lineup) == 7 and lineup_roles is None:
        st.error("Die Auswahl braucht 1 Zuspieler, 1 Dia, 2 Aussen, 2 Mitten und 1 Libero.")
    first_server = st.segmented_control(
        "Wer hat im ersten Satz zuerst Service?",
        options=["us", "opponent"],
        default="opponent",
        format_func=lambda value: "Wir haben Service" if value == "us" else "Gegner hat Service",
        key="new_match_first_server",
    )
    starting_rotation = st.segmented_control(
        "In welchem Läufer starten wir?",
        options=[1, 2, 3, 4, 5, 6],
        default=1,
        format_func=lambda value: f"L{value}",
        key="new_match_rotation",
    )
    positions: dict[str, str] = {}
    if len(lineup) == 7 and lineup_roles:
        rotational_ids = {
            lineup_roles["setter"],
            lineup_roles["opposite"],
            *lineup_roles["outsides"],
            *lineup_roles["middles"],
        }
        rotational_players = [player for player in lineup if player.id in rotational_ids]
        st.markdown("#### Startpositionen")
        libero_name = player_map[lineup_roles["libero"]].name
        st.caption(f"{libero_name} ist der Libero und ersetzt automatisch die hintere Mitte.")
        positions = _render_position_assignment(
            rotational_players,
            starting_rotation,
            key_prefix="new_match_position",
            lineup_roles=lineup_roles,
        )
    else:
        st.info("Wähle die 7 Spieler in der geforderten Rollenverteilung.")

    if st.button("Match starten", type="primary", use_container_width=True, key="start_live_match"):
        if not opponent.strip():
            st.error("Bitte den Gegner eintragen.")
        elif len(lineup) != 7 or lineup_roles is None:
            st.error("Bitte genau 7 Spieler in der geforderten Rollenverteilung auswählen.")
        elif len(set(positions.values())) != 6:
            st.error("Jeder Spieler darf nur auf einer Startposition stehen.")
        elif positions.get(str(starting_rotation)) != lineup_roles["setter"]:
            st.error(f"Bei L{starting_rotation} muss der Zuspieler auf Position {starting_rotation} stehen.")
        elif not first_server:
            st.error("Bitte das erste Aufschlagrecht auswählen.")
        else:
            analysis_state = new_match_state(
                first_server,
                starting_rotation=starting_rotation,
                positions=positions,
                lineup_roles=lineup_roles,
            )
            analysis_state["analysis_video_session_id"] = selected_source_id
            session_id = create_match_session(
                match_date=match_date.isoformat(),
                opponent=opponent,
                lineup_player_ids=[player.id for player in lineup],
                state=analysis_state,
            )
            st.session_state.live_match_session_id = session_id
            st.rerun()


def _render_scoreboard(session: dict[str, Any]) -> None:
    state = session["state"]
    st.markdown(
        f"### Satz {state['current_set']} · Ziel {set_target(state['current_set'])}, zwei Punkte Abstand"
    )
    our_score, set_score, opponent_score = st.columns([2, 1, 2])
    our_score.metric(TEAM_NAME, state["our_score"])
    set_score.metric("Sätze", f"{state['our_sets']} : {state['opponent_sets']}")
    opponent_score.metric(session["opponent"], state["opponent_score"])
    if state["completed_sets"]:
        set_results = " · ".join(
            f"Satz {item['set_number']}: {item['our_score']}:{item['opponent_score']}"
            for item in state["completed_sets"]
        )
        st.caption(set_results)


def _render_lineup_controls(
    session: dict[str, Any], roster: tuple[Any, ...], player_label: Callable[[Any], str]
) -> None:
    players = _players_by_id(roster)
    current_lineup = [
        players[player_id] for player_id in session["lineup_player_ids"] if player_id in players
    ]
    names = " · ".join(player.name for player in current_lineup)
    state = session["state"]
    positions = state.get("positions", {})
    position_text = " · ".join(
        f"P{position} {players[player_id].name}"
        for position, player_id in sorted(positions.items(), key=lambda item: int(item[0]))
        if player_id in players
    )
    st.caption(f"Matchkader: {names}")
    st.caption(f"Rotationsordnung L{state.get('current_rotation', 1)} · {position_text}")
    if state.get("substitutions"):
        last_change = state["substitutions"][-1]
        st.caption(
            f"Letzter Wechsel: {last_change['outgoing_name']} → {last_change['incoming_name']} · "
            f"Satz {last_change['set_number']} bei {last_change['our_score']}:{last_change['opponent_score']}"
        )
    current_ids = {player.id for player in current_lineup}
    bench_players = [player for player in roster if player.id not in current_ids]
    lineup_roles = state.get("lineup_roles") or {}
    eligible_outgoing = [
        player
        for player in current_lineup
        if (role := lineup_role_for_player(lineup_roles, player.id))
        and any(player_can_play_role(candidate, role) for candidate in bench_players)
    ]

    control_a, control_b, control_c = st.columns(3)
    with control_a.popover("Spieler wechseln", use_container_width=True):
        if not eligible_outgoing:
            st.info("Für den aktuellen Kader ist kein positionsgleicher Wechsel möglich.")
        else:
            outgoing = st.selectbox(
                "Raus",
                options=eligible_outgoing,
                format_func=lambda player: (
                    f"{player.name} · {MATCH_ROLE_LABELS[lineup_role_for_player(lineup_roles, player.id)]}"
                ),
                key=f"sub_out_{session['id']}",
            )
            outgoing_role = lineup_role_for_player(lineup_roles, outgoing.id)
            incoming_options = [
                player for player in bench_players if player_can_play_role(player, outgoing_role)
            ]
            incoming = st.selectbox(
                "Rein",
                options=incoming_options,
                format_func=player_label,
                key=f"sub_in_{session['id']}_{outgoing.id}",
            )
            st.caption(
                f"{incoming.name} übernimmt die Rolle {MATCH_ROLE_LABELS[outgoing_role]} "
                "und denselben Rotationsplatz."
            )
            if st.button(
                f"{outgoing.name} → {incoming.name} wechseln",
                type="primary",
                use_container_width=True,
                key=f"save_sub_{session['id']}_{outgoing.id}_{incoming.id}",
            ):
                updated_state = substitute_match_player(
                    state,
                    outgoing.id,
                    incoming.id,
                    outgoing_name=outgoing.name,
                    incoming_name=incoming.name,
                )
                updated_lineup_ids = [
                    incoming.id if player_id == outgoing.id else player_id
                    for player_id in session["lineup_player_ids"]
                ]
                _save_state(
                    session["id"],
                    updated_state,
                    lineup_player_ids=updated_lineup_ids,
                )
                st.rerun()

    with control_b.popover("Kader korrigieren", use_container_width=True):
        changed_lineup = st.multiselect(
            "7er-Matchkader",
            options=list(roster),
            default=current_lineup,
            format_func=player_label,
            key=f"lineup_{session['id']}",
        )
        if st.button("Aufstellung übernehmen", key=f"save_lineup_{session['id']}", use_container_width=True):
            changed_roles = assign_lineup_roles(changed_lineup)
            if len(changed_lineup) != 7 or changed_roles is None:
                st.error("Bitte 1 Zuspieler, 1 Dia, 2 Aussen, 2 Mitten und 1 Libero auswählen.")
            else:
                updated_state = deepcopy(session["state"])
                old_roles = updated_state.get("lineup_roles", {})
                replacement_map: dict[str, str] = {}
                for role in ("setter", "opposite", "libero"):
                    if old_roles.get(role):
                        replacement_map[old_roles[role]] = changed_roles[role]
                for role in ("outsides", "middles"):
                    for old_id, new_id in zip(old_roles.get(role, []), changed_roles[role]):
                        replacement_map[old_id] = new_id
                updated_state["lineup_roles"] = changed_roles
                for slots_key in ("rotation_slots", "starting_rotation_slots"):
                    updated_state[slots_key] = {
                        position: replacement_map.get(player_id, player_id)
                        for position, player_id in updated_state.get(slots_key, {}).items()
                    }
                updated_state["positions"] = apply_libero_substitution(
                    updated_state["rotation_slots"],
                    changed_roles,
                    updated_state["serving_team"],
                )
                _save_state(
                    session["id"],
                    updated_state,
                    lineup_player_ids=[player.id for player in changed_lineup],
                )
                st.rerun()
    if control_c.button("Anderes Match", use_container_width=True, key="leave_live_match"):
        st.session_state.pop("live_match_session_id", None)
        st.rerun()


def _render_our_service(session: dict[str, Any], roster: tuple[Any, ...]) -> None:
    state = session["state"]
    player_map = _players_by_id(roster)
    server_id = state.get("positions", {}).get("1")
    if server_id not in player_map:
        st.error("Die Spieler auf P1 konnte nicht bestimmt werden.")
        return
    server = player_map[server_id]

    context = (
        f"service_{session['id']}_{state['current_set']}_{state['rally_number']}_{state['sequence_number']}"
    )
    st.markdown("### Eigener Service")
    st.caption(
        f"{server.name} serviert automatisch, weil sie auf P1 steht. "
        "Bei Ass oder Fehler wird der Punkt sofort verbucht."
    )
    service_type = st.segmented_control(
        "Wie serviert sie?",
        options=list(SERVICE_TYPE_LABELS),
        default="standing",
        format_func=lambda value: SERVICE_TYPE_LABELS[value],
        key=f"{context}_type",
    )
    service_result = st.segmented_control(
        "Wie war der Service?",
        options=list(SERVICE_RESULT_OPTIONS),
        default="okay",
        format_func=lambda value: SERVICE_RESULT_LABELS[value],
        key=f"{context}_result",
    )
    service_origin, service_target = _render_service_placement_picker(context=context)
    save_col, skip_col = st.columns(2)
    if save_col.button("Service speichern", type="primary", width="stretch", key=f"{context}_save"):
        if not service_type or not service_result or service_origin is None or service_target is None:
            st.error("Bitte Serviceart, Bewertung, Serviceort und Ziel auswählen.")
            return
        save_match_action(
            session_id=session["id"],
            rally_number=state["rally_number"],
            sequence_number=state["sequence_number"],
            match_date=session["match_date"],
            opponent=session["opponent"],
            set_number=state["current_set"],
            ball_type="service",
            receiver_id="",
            receiver_name="",
            first_contact_quality="",
            setter_involved=False,
            server_id=server.id,
            server_name=server.name,
            service_type=service_type,
            service_result=service_result,
            service_origin_x=service_origin,
            service_origin_y=0,
            landing_x=service_target[0],
            landing_y=service_target[1],
            landing_out=landing_cell_is_out(*service_target),
        )
        if service_result == "ace":
            _award_and_refresh(
                session,
                "us",
                f"Serviceass {server.name}",
                result_kind="point",
            )
        elif service_result == "error":
            _award_and_refresh(
                session,
                "opponent",
                f"Servicefehler {server.name}",
                result_kind="our_error",
            )
        else:
            _save_state(session["id"], continue_to_opponent(state))
            st.rerun()
    if skip_col.button("Service überspringen", width="stretch", key=f"{context}_skip"):
        _save_state(session["id"], continue_to_opponent(state))
        st.rerun()


def _render_opponent_turn(session: dict[str, Any], roster: tuple[Any, ...]) -> None:
    state = session["state"]
    if state["serving_team"] == "us" and state["sequence_number"] == 1:
        _render_our_service(session, roster)
        return

    st.info("Der Gegner ist am Ball. Was kommt als Nächstes?")

    attack_col, freeball_col = st.columns(2)
    if attack_col.button("Angriff kommt", type="primary", use_container_width=True, key="opponent_attack"):
        _save_state(session["id"], start_block_evaluation(state))
        st.rerun()
    if freeball_col.button("Gratisball kommt", use_container_width=True, key="opponent_freeball"):
        _save_state(session["id"], receive_opponent_ball(state, "freeball"))
        st.rerun()

    error_col, point_col = st.columns(2)
    if error_col.button("Gegnerfehler · Punkt für uns", use_container_width=True, key="opponent_error"):
        _award_and_refresh(session, "us", "Gegnerfehler", result_kind="opponent_error")
    if point_col.button("Gegnerpunkt", use_container_width=True, key="opponent_point"):
        _award_and_refresh(session, "opponent", "Gegnerpunkt", result_kind="opponent_point")


def _render_block_evaluation(session: dict[str, Any], roster: tuple[Any, ...]) -> None:
    state = session["state"]
    player_map = _players_by_id(roster)
    context = (
        f"block_{session['id']}_{state['current_set']}_{state['rally_number']}_{state['sequence_number']}"
    )
    st.markdown("### Block gegen den gegnerischen Angriff")
    opponent_attack_origin = st.segmented_control(
        "Wo greift der Gegner an?",
        options=list(OPPONENT_ATTACK_ORIGIN_LABELS),
        default="outside",
        format_func=lambda value: OPPONENT_ATTACK_ORIGIN_LABELS[value],
        key=f"{context}_opponent_origin",
    )
    eligible_ids = eligible_blocker_ids(state, opponent_attack_origin)
    front_players = [player_map[player_id] for player_id in eligible_ids if player_id in player_map]
    if not front_players:
        st.error("Die möglichen Blocker konnten nicht bestimmt werden.")
        return
    system_positions = system_court_positions(state)
    position_by_player = {player_id: position for position, player_id in system_positions.items()}
    st.caption(
        "Zur Auswahl stehen nur die Spieler, die diesen Angriff im aktuellen System "
        "wirklich blocken können."
    )
    block_result = st.segmented_control(
        "Was passiert am Block?",
        options=list(BLOCK_RESULT_LABELS),
        default="no_touch",
        format_func=lambda value: BLOCK_RESULT_LABELS[value],
        key=f"{context}_result",
    )
    formation_options = ["closed", "middle_late"]
    if block_result == "no_touch":
        formation_options.append("not_needed")
    block_formation = st.segmented_control(
        "Wie stand der Block?",
        options=formation_options,
        default="closed",
        format_func=lambda value: BLOCK_FORMATION_LABELS[value],
        key=f"{context}_formation",
    )
    if block_formation == "not_needed":
        st.info("Zum Beispiel bei einem Gratisball: Danach wird direkt der erste Ball erfasst.")
    blocker_required = block_result in {"touch", "point", "error"} or (
        block_result == "no_touch" and block_formation == "middle_late"
    )
    selectable_blockers = front_players
    if block_result == "no_touch" and block_formation == "middle_late":
        middle_ids = set(state.get("lineup_roles", {}).get("middles", []))
        selectable_blockers = [player for player in front_players if player.id in middle_ids]
    blocker_id = ""
    if blocker_required:
        default_blocker = _default_player(selectable_blockers, ("middle", "outside", "opposite", "setter"))
        blocker_id = st.pills(
            "Welche Spieler bewerten wir am Block?",
            options=[player.id for player in selectable_blockers],
            default=default_blocker.id,
            format_func=lambda player_id: (
                f"{player_map[player_id].name} · P{position_by_player.get(player_id, '?')}"
            ),
            key=f"{context}_player",
        )
    else:
        st.caption("Kein Blocktouch wird als Teamaktion gespeichert – kein Spieler nötig.")

    if st.button("Blockaktion speichern", type="primary", use_container_width=True, key=f"{context}_save"):
        blocker = player_map.get(blocker_id) if blocker_id else None
        if blocker_required and blocker is None:
            st.error("Bitte eine Spieler für die Blockaktion auswählen.")
            return
        if not block_result or not block_formation:
            st.error("Bitte Blockergebnis und Blockstellung auswählen.")
            return
        save_match_action(
            session_id=session["id"],
            rally_number=state["rally_number"],
            sequence_number=state["sequence_number"],
            match_date=session["match_date"],
            opponent=session["opponent"],
            set_number=state["current_set"],
            ball_type="block",
            receiver_id="",
            receiver_name="",
            first_contact_quality="",
            setter_involved=False,
            opponent_attack_origin=opponent_attack_origin,
            block_player_id=blocker.id if blocker else "",
            block_player_name=blocker.name if blocker else "",
            block_result=block_result,
            block_formation=block_formation,
        )
        if block_result == "point":
            _award_and_refresh(session, "us", f"Blockpunkt {blocker.name if blocker else 'Teamblock'}")
        elif block_result == "error":
            _award_and_refresh(
                session,
                "opponent",
                f"Blockfehler {blocker.name if blocker else 'Teamblock'}",
                result_kind="our_error",
            )
        else:
            next_ball_type = "freeball" if block_formation == "not_needed" else "attack_defense"
            _save_state(session["id"], receive_opponent_ball(state, next_ball_type))
            st.rerun()


@st.fragment
def _render_our_contact_all_at_once(session: dict[str, Any], roster: tuple[Any, ...]) -> None:
    state = session["state"]
    player_map = _players_by_id(roster)
    field_player_ids = list(dict.fromkeys(state.get("positions", {}).values()))
    if not field_player_ids:
        field_player_ids = session["lineup_player_ids"][:6]
    field_players = [player_map[player_id] for player_id in field_player_ids if player_id in player_map]
    ball_type = state["phase"]
    is_block_recycle = ball_type == "block_recycle"
    context = (
        f"live_{session['id']}_{state['current_set']}_{state['rally_number']}_{state['sequence_number']}"
    )

    receiver = None
    first_quality = "not_rated"
    if is_block_recycle:
        st.markdown("### Blockball wieder bei uns")
        st.info(
            "Der Ball kommt vom gegnerischen Block zurück. Wir erfassen direkt Pass und Angriff – ohne Annahmebewertung."
        )
    else:
        st.markdown(f"### {BALL_TYPE_LABELS[ball_type]}")
        default_receiver = _default_player(
            field_players, ("libero", "outside", "opposite", "middle", "setter")
        )
        receiver_id = st.pills(
            "Wer nimmt den Ball?",
            options=[player.id for player in field_players],
            default=default_receiver.id,
            format_func=lambda player_id: player_map[player_id].name,
            key=f"{context}_receiver",
        )
        first_quality = st.segmented_control(
            "Wie ist die Annahme / Abwehr?",
            options=list(FIRST_CONTACT_LABELS),
            default="good",
            format_func=lambda value: FIRST_CONTACT_LABELS[value].split(" · ")[0],
            key=f"{context}_quality",
        )
        receiver = player_map.get(receiver_id) if receiver_id else None
        st.caption(
            "Perfekt = 3 Angreifer · Gut = 2 · Okay = noch passbar · "
            "Schlecht = kaum spielbar · Annahmefehler = direkter Punkt"
        )

    if not is_block_recycle and first_quality == "error":
        if st.button(
            "Annahmefehler speichern · Punkt Gegner",
            type="primary",
            use_container_width=True,
            key=f"{context}_reception_error",
        ):
            if receiver is None:
                st.error("Bitte den Spieler auswählen.")
            else:
                save_match_action(
                    session_id=session["id"],
                    rally_number=state["rally_number"],
                    sequence_number=state["sequence_number"],
                    match_date=session["match_date"],
                    opponent=session["opponent"],
                    set_number=state["current_set"],
                    ball_type=ball_type,
                    receiver_id=receiver.id,
                    receiver_name=receiver.name,
                    first_contact_quality=first_quality,
                    setter_involved=False,
                )
                _award_and_refresh(
                    session,
                    "opponent",
                    f"Annahmefehler {receiver.name}",
                    result_kind="our_error",
                )
        return

    direct_return = (
        False
        if is_block_recycle
        else st.toggle(
            "Der erste Ball geht direkt zum Gegner zurück",
            value=False,
            key=f"{context}_direct_return",
        )
    )
    if direct_return:
        direct_origin = attack_origin_for_player(state, receiver.id if receiver else "")
        direct_block_outcome = _render_attack_block_outcome(context=f"{context}_direct_return")
        direct_target = None
        if direct_block_outcome in {"none", "touch_opponent"}:
            direct_target = _render_landing_picker(
                context=f"{context}_direct_return",
                attack_origin=direct_origin,
                attack_type="direct_return",
            )
        direct_result = attack_result_for_block_outcome(
            direct_block_outcome,
            _render_attack_result(
                context=f"{context}_direct_return",
                selected_cell=direct_target,
                label="Wie endet der direkte Ball?",
                attack_block_outcome=direct_block_outcome,
            ),
        )
        if st.button(
            "Direkten Ball speichern",
            type="primary",
            use_container_width=True,
            key=f"{context}_save_direct_return",
        ):
            target_required = direct_block_outcome in {"none", "touch_opponent"}
            if (
                receiver is None
                or not first_quality
                or not direct_origin
                or (target_required and direct_target is None)
                or not direct_result
            ):
                st.error(
                    "Bitte Spieler, Qualität, Angriffsposition, Blockausgang, Zielfeld und Ergebnis auswählen."
                )
            else:
                save_match_action(
                    session_id=session["id"],
                    rally_number=state["rally_number"],
                    sequence_number=state["sequence_number"],
                    match_date=session["match_date"],
                    opponent=session["opponent"],
                    set_number=state["current_set"],
                    ball_type=ball_type,
                    receiver_id=receiver.id,
                    receiver_name=receiver.name,
                    first_contact_quality=first_quality,
                    setter_involved=False,
                    attacker_id=receiver.id,
                    attacker_name=receiver.name,
                    attack_type="direct_return",
                    attack_result=direct_result,
                    attack_block_outcome=direct_block_outcome,
                    attack_origin=direct_origin,
                    landing_x=direct_target[0] if direct_target else None,
                    landing_y=direct_target[1] if direct_target else None,
                    landing_out=landing_cell_is_out(*direct_target) if direct_target else False,
                )
                _finish_our_attack(
                    session,
                    state,
                    attacker_name=receiver.name,
                    attack_result=direct_result,
                    attack_block_outcome=direct_block_outcome,
                )
        return

    st.markdown("### Zuspiel")
    default_setter = _default_player(field_players, ("setter", "libero", "outside", "opposite", "middle"))
    passer_id = st.pills(
        "Wer spielt den zweiten Ball?",
        options=[player.id for player in field_players],
        default=default_setter.id,
        format_func=lambda player_id: player_map[player_id].name,
        key=f"{context}_passer",
    )
    passer = player_map.get(passer_id) if passer_id else None
    is_setter = bool(passer and (passer.primary_position == "setter" or passer.backup_setter))
    setter_movement = ""
    if is_setter:
        setter_movement = st.segmented_control(
            "War der Zuspieler rechtzeitig unter dem Ball?",
            options=list(SETTER_MOVEMENT_LABELS),
            default="fast",
            format_func=lambda value: SETTER_MOVEMENT_LABELS[value],
            key=f"{context}_movement",
        )
    elif passer:
        st.caption(f"{passer.name} spielt als Nicht-Zuspieler den zweiten Ball.")

    second_ball_action = "set"
    if is_setter:
        second_ball_action = st.segmented_control(
            "Was macht der Zuspieler mit dem zweiten Ball?",
            options=["set", "setter_tip"],
            default="set",
            format_func=lambda value: "Pass" if value == "set" else "Zuspielerfinte",
            key=f"{context}_second_ball_action",
        )

    if second_ball_action == "setter_tip":
        setter_tip_origin = attack_origin_for_player(state, passer.id if passer else "")
        setter_tip_block_outcome = _render_attack_block_outcome(context=f"{context}_setter_tip")
        setter_tip_target = None
        if setter_tip_block_outcome in {"none", "touch_opponent"}:
            setter_tip_target = _render_landing_picker(
                context=f"{context}_setter_tip",
                attack_origin=setter_tip_origin,
                attack_type="setter_tip",
            )
        setter_tip_result = attack_result_for_block_outcome(
            setter_tip_block_outcome,
            _render_attack_result(
                context=f"{context}_setter_tip",
                selected_cell=setter_tip_target,
                label="Wie endet die Zuspielerfinte?",
                attack_block_outcome=setter_tip_block_outcome,
            ),
        )
        if st.button(
            "Zuspielerfinte speichern",
            type="primary",
            use_container_width=True,
            key=f"{context}_save_setter_tip",
        ):
            if (
                (not is_block_recycle and receiver is None)
                or passer is None
                or not first_quality
                or not setter_tip_origin
                or (setter_tip_block_outcome in {"none", "touch_opponent"} and setter_tip_target is None)
                or not setter_tip_result
            ):
                st.error(
                    "Bitte Kontaktkette, Angriffsposition, Zielfeld und Ergebnis der Zuspielerfinte auswählen."
                )
            else:
                save_match_action(
                    session_id=session["id"],
                    rally_number=state["rally_number"],
                    sequence_number=state["sequence_number"],
                    match_date=session["match_date"],
                    opponent=session["opponent"],
                    set_number=state["current_set"],
                    ball_type=ball_type,
                    receiver_id=receiver.id if receiver else "",
                    receiver_name=receiver.name if receiver else "",
                    first_contact_quality=first_quality,
                    setter_involved=True,
                    setter_id=passer.id,
                    setter_name=passer.name,
                    setter_movement=setter_movement,
                    attacker_id=passer.id,
                    attacker_name=passer.name,
                    attack_type="setter_tip",
                    attack_result=setter_tip_result,
                    attack_block_outcome=setter_tip_block_outcome,
                    attack_origin=setter_tip_origin,
                    landing_x=setter_tip_target[0] if setter_tip_target else None,
                    landing_y=setter_tip_target[1] if setter_tip_target else None,
                    landing_out=landing_cell_is_out(*setter_tip_target) if setter_tip_target else False,
                )
                _finish_our_attack(
                    session,
                    state,
                    attacker_name=passer.name,
                    attack_result=setter_tip_result,
                    attack_block_outcome=setter_tip_block_outcome,
                )
        return

    rating_options = set_quality_options(first_quality)
    set_quality = st.segmented_control(
        "Wie ist der Pass?",
        options=list(rating_options),
        default=rating_options[0],
        format_func=lambda value: SET_QUALITY_LABELS[value],
        key=f"{context}_set_quality_{'normal' if first_quality in {'perfect', 'good'} else 'difficult'}",
    )
    st.markdown("### Angriff")
    attackers = [player for player in field_players if passer is None or player.id != passer.id]
    default_attacker = _default_player(attackers, ("outside", "opposite", "middle", "setter", "libero"))
    attacker_id = st.pills(
        "Wer greift an?",
        options=[player.id for player in attackers],
        default=default_attacker.id,
        format_func=lambda player_id: player_map[player_id].name,
        key=f"{context}_attacker",
    )
    attacker = player_map.get(attacker_id) if attacker_id else None
    attack_type_options = attack_type_options_for_player(
        attacker.id if attacker else "",
        state.get("lineup_roles"),
    )
    attack_type = st.segmented_control(
        "Was macht sie?",
        options=list(attack_type_options),
        default=attack_type_options[0],
        format_func=lambda value: ATTACK_TYPE_LABELS[value],
        disabled=len(attack_type_options) == 1,
        key=(f"{context}_libero_attack_type" if len(attack_type_options) == 1 else f"{context}_attack_type"),
    )
    if len(attack_type_options) == 1:
        st.caption(
            "Der Libero darf den dritten Ball als Safe Ball übers Netz spielen, solange der Ball beim "
            "Kontakt nicht vollständig über der Netzkante ist."
        )
    attack_origin = attack_origin_for_player(state, attacker.id if attacker else "")
    attack_context = f"{context}_{attacker.id if attacker else 'none'}_{attack_type or 'attack'}"
    attack_block_outcome = _render_attack_block_outcome(context=attack_context)
    attack_target = None
    if attack_block_outcome in {"none", "touch_opponent"}:
        attack_target = _render_landing_picker(
            context=f"{context}_{attacker.id if attacker else 'none'}",
            attack_origin=attack_origin,
            attack_type=attack_type or "attack",
        )
    attack_result = attack_result_for_block_outcome(
        attack_block_outcome,
        _render_attack_result(
            context=attack_context,
            selected_cell=attack_target,
            label="Wie endet unser Angriff?",
            attack_block_outcome=attack_block_outcome,
        ),
    )

    if st.button("Aktion speichern", type="primary", use_container_width=True, key=f"{context}_save"):
        if (
            (not is_block_recycle and receiver is None)
            or passer is None
            or attacker is None
            or not first_quality
            or not set_quality
            or not attack_type
            or not attack_origin
            or (attack_block_outcome in {"none", "touch_opponent"} and attack_target is None)
            or not attack_result
        ):
            st.error("Bitte Kontaktkette, Angriffsposition, Zielfeld und Ergebnis vollständig auswählen.")
        else:
            save_match_action(
                session_id=session["id"],
                rally_number=state["rally_number"],
                sequence_number=state["sequence_number"],
                match_date=session["match_date"],
                opponent=session["opponent"],
                set_number=state["current_set"],
                ball_type=ball_type,
                receiver_id=receiver.id if receiver else "",
                receiver_name=receiver.name if receiver else "",
                first_contact_quality=first_quality,
                setter_involved=True,
                setter_id=passer.id,
                setter_name=passer.name,
                setter_movement=setter_movement,
                set_quality=set_quality,
                attacker_id=attacker.id,
                attacker_name=attacker.name,
                attack_type=attack_type,
                attack_result=attack_result,
                attack_block_outcome=attack_block_outcome,
                attack_origin=attack_origin,
                landing_x=attack_target[0] if attack_target else None,
                landing_y=attack_target[1] if attack_target else None,
                landing_out=landing_cell_is_out(*attack_target) if attack_target else False,
            )
            _finish_our_attack(
                session,
                state,
                attacker_name=attacker.name,
                attack_result=attack_result,
                attack_block_outcome=attack_block_outcome,
            )


UNASSIGNED_PLAYER_ID = "__unassigned__"
NO_RECEIVER_PLAYER_ID = "__nobody__"


def first_contact_player_options(field_players: Iterable[Any]) -> list[str]:
    """Keep the two team-level choices visible before the player names."""

    return [
        NO_RECEIVER_PLAYER_ID,
        UNASSIGNED_PLAYER_ID,
        *[player.id for player in field_players],
    ]


@st.fragment
def _render_our_contact(session: dict[str, Any], roster: tuple[Any, ...]) -> None:
    state = session["state"]
    player_map = _players_by_id(roster)
    field_player_ids = list(dict.fromkeys(state.get("positions", {}).values()))
    if not field_player_ids:
        field_player_ids = session["lineup_player_ids"][:6]
    field_players = [player_map[player_id] for player_id in field_player_ids if player_id in player_map]
    if not field_players:
        st.error("Die Spieler auf dem Feld konnten nicht bestimmt werden.")
        return

    ball_type = state["phase"]
    is_block_recycle = ball_type == "block_recycle"
    context = (
        f"step_live_{session['id']}_{state['current_set']}_{state['rally_number']}_{state['sequence_number']}"
    )
    step_key = f"{context}_step"
    pending_key = f"{context}_pending"
    initial_step = 2 if is_block_recycle else 1
    if st.session_state.get(step_key) not in {1, 2, 3}:
        st.session_state[step_key] = initial_step
    if pending_key not in st.session_state:
        st.session_state[pending_key] = {
            "receiver_id": "",
            "receiver_name": "",
            "first_contact_quality": "not_rated" if is_block_recycle else "",
            "first_contact_x": None,
            "first_contact_y": None,
            "first_contact_too_low": False,
            "passer_id": "",
            "passer_name": "",
            "setter_involved": False,
            "setter_movement": "",
            "set_quality": "not_rated",
            "set_tendency": "not_rated",
            "set_inside_meters": 0.0,
            "set_origin": "",
            "set_origin_x": None,
            "set_origin_y": None,
            "second_skipped": False,
        }
    step = int(st.session_state[step_key])
    pending = dict(st.session_state[pending_key])

    def player_choice_label(player_id: str) -> str:
        if player_id == UNASSIGNED_PLAYER_ID:
            return "Nicht zuordnen"
        if player_id == NO_RECEIVER_PLAYER_ID:
            return "Keiner"
        return player_map[player_id].name

    def advance(next_step: int, **updates: Any) -> None:
        pending.update(updates)
        st.session_state[pending_key] = pending
        st.session_state[step_key] = next_step
        st.rerun(scope="app")

    def clear_flow() -> None:
        st.session_state.pop(step_key, None)
        st.session_state.pop(pending_key, None)

    def save_pending_action(**action: Any) -> None:
        save_match_action(
            session_id=session["id"],
            rally_number=state["rally_number"],
            sequence_number=state["sequence_number"],
            match_date=session["match_date"],
            opponent=session["opponent"],
            set_number=state["current_set"],
            ball_type=ball_type,
            receiver_id=pending.get("receiver_id", ""),
            receiver_name=pending.get("receiver_name", ""),
            first_contact_quality=pending.get("first_contact_quality", "not_rated"),
            first_contact_x=pending.get("first_contact_x"),
            first_contact_y=pending.get("first_contact_y"),
            first_contact_too_low=bool(pending.get("first_contact_too_low")),
            setter_involved=bool(pending.get("setter_involved")),
            setter_id=pending.get("passer_id", ""),
            setter_name=pending.get("passer_name", ""),
            setter_movement=pending.get("setter_movement", ""),
            set_quality=pending.get("set_quality", "not_rated"),
            set_tendency=",".join(parse_set_tendencies(pending.get("set_tendency"))),
            set_inside_meters=float(pending.get("set_inside_meters", 0.0)),
            set_origin=pending.get("set_origin", ""),
            set_origin_x=pending.get("set_origin_x"),
            set_origin_y=pending.get("set_origin_y"),
            **action,
        )

    if step == 1:
        st.markdown(f"### 1 · {BALL_TYPE_LABELS[ball_type]}")
        st.caption("Zuerst wird nur der erste Ball erfasst. Danach erscheint der zweite Ball.")
        if ball_type == "serve_receive" and st.button(
            "Servicefehler Gegner · Punkt für uns",
            use_container_width=True,
            key=f"{context}_opponent_service_error",
        ):
            clear_flow()
            _award_and_refresh(
                session,
                "us",
                "Servicefehler Gegner",
                result_kind="opponent_error",
            )
            return
        default_receiver = _default_player(
            field_players, ("libero", "outside", "opposite", "middle", "setter")
        )
        receiver_choice = st.pills(
            "Wer nimmt den Ball?",
            options=first_contact_player_options(field_players),
            default=default_receiver.id,
            format_func=player_choice_label,
            key=f"{context}_receiver",
        )
        receiver = player_map.get(receiver_choice) if receiver_choice else None

        if receiver_choice == NO_RECEIVER_PLAYER_ID:
            no_contact_reason = st.segmented_control(
                "Warum nimmt niemand den Ball?",
                options=list(NO_CONTACT_REASON_OPTIONS),
                default=None,
                format_func=lambda reason: no_contact_reason_label(ball_type, reason),
                key=f"{context}_no_contact_reason",
            )
            communication_player_ids: list[str] = []
            if no_contact_reason == "communication":
                communication_player_ids = list(
                    st.pills(
                        "Zwischen welchen Spieler stimmt die Kommunikation nicht?",
                        options=[player.id for player in field_players],
                        default=[],
                        format_func=lambda player_id: player_map[player_id].name,
                        selection_mode="multi",
                        key=f"{context}_communication_players",
                    )
                    or []
                )
                if len(communication_player_ids) < 2:
                    st.caption("Bitte mindestens zwei beteiligte Spieler auswählen.")

            can_save_no_contact = bool(
                no_contact_reason
                and (
                    no_contact_reason != "communication"
                    or len(communication_player_ids) >= 2
                )
            )
            if st.button(
                "Keinen ersten Kontakt speichern · Punkt Gegner",
                type="primary",
                width="stretch",
                disabled=not can_save_no_contact,
                key=f"{context}_save_no_contact",
            ):
                communication_names = [
                    player_map[player_id].name
                    for player_id in communication_player_ids
                    if player_id in player_map
                ]
                save_match_action(
                    session_id=session["id"],
                    rally_number=state["rally_number"],
                    sequence_number=state["sequence_number"],
                    match_date=session["match_date"],
                    opponent=session["opponent"],
                    set_number=state["current_set"],
                    ball_type=ball_type,
                    receiver_id="",
                    receiver_name="",
                    first_contact_quality="not_rated",
                    setter_involved=False,
                    no_contact_reason=no_contact_reason or "",
                    communication_player_ids=communication_player_ids,
                    communication_player_names=communication_names,
                )
                reason_label = no_contact_reason_label(
                    ball_type,
                    no_contact_reason or "",
                )
                if communication_names:
                    reason_label += " · " + " / ".join(communication_names)
                clear_flow()
                _award_and_refresh(
                    session,
                    "opponent",
                    reason_label,
                    result_kind="our_error",
                )
            return

        first_ball_direct = st.toggle(
            "Der erste Ball geht direkt zum Gegner zurück",
            value=False,
            key=f"{context}_first_ball_direct",
        )
        first_contact_target = None
        first_quality: str | None = "not_rated" if first_ball_direct else None
        first_contact_too_low = False
        too_low_key = f"{context}_first_contact_too_low"
        target_source_key = f"{context}_first_contact_too_low_target"
        if not first_ball_direct:
            first_contact_target = _render_first_contact_target_picker(context=context)
            if first_contact_target is not None:
                if st.session_state.get(target_source_key) != first_contact_target:
                    st.session_state[target_source_key] = first_contact_target
                    st.session_state[too_low_key] = False
                base_quality = suggested_first_contact_quality(*first_contact_target)
                if base_quality == "error":
                    st.session_state[too_low_key] = False
                    first_quality = "error"
                    st.error("Automatische Bewertung: **Annahmefehler**")
                else:
                    first_contact_too_low = st.toggle(
                        "Annahme war zu tief",
                        value=False,
                        key=too_low_key,
                        help="Zu tief senkt die automatische Bewertung um eine Qualitätsstufe.",
                    )
                    first_quality = suggested_first_contact_quality(
                        *first_contact_target,
                        too_low=first_contact_too_low,
                    )
                    st.info(
                        f"Automatische Bewertung: **{FIRST_CONTACT_LABELS[first_quality].split(' · ')[0]}**"
                    )
        st.caption(
            "Du wählst nur Zielort und gegebenenfalls «zu tief». "
            "Mit Nicht zuordnen wird die Bewertung als Team-Abnahme gespeichert; "
            "mit Überspringen wird keine Abnahme gespeichert."
        )
        if first_quality == "error":
            if st.button(
                "Annahmefehler speichern · Punkt Gegner",
                type="primary",
                width="stretch",
                key=f"{context}_reception_error",
            ):
                save_match_action(
                    session_id=session["id"],
                    rally_number=state["rally_number"],
                    sequence_number=state["sequence_number"],
                    match_date=session["match_date"],
                    opponent=session["opponent"],
                    set_number=state["current_set"],
                    ball_type=ball_type,
                    receiver_id=receiver.id if receiver else "",
                    receiver_name=receiver.name if receiver else "",
                    first_contact_quality="error",
                    first_contact_x=first_contact_target[0] if first_contact_target else None,
                    first_contact_y=first_contact_target[1] if first_contact_target else None,
                    first_contact_too_low=False,
                    setter_involved=False,
                )
                error_name = receiver.name if receiver else "Team"
                clear_flow()
                _award_and_refresh(
                    session,
                    "opponent",
                    f"Annahmefehler {error_name}",
                    result_kind="our_error",
                )
            if st.button("Abnahme stattdessen überspringen", width="stretch", key=f"{context}_skip_error"):
                advance(
                    2,
                    receiver_id="",
                    receiver_name="",
                    first_contact_quality="not_rated",
                    first_contact_x=None,
                    first_contact_y=None,
                    first_contact_too_low=False,
                )
            return
        if first_ball_direct:
            direct_origin = attack_origin_for_player(state, receiver.id if receiver else "")
            direct_block_outcome = _render_attack_block_outcome(context=f"{context}_first_direct")
            direct_target = None
            if direct_block_outcome in {"none", "touch_opponent"}:
                direct_target = _render_landing_picker(
                    context=f"{context}_first_direct",
                    attack_origin=direct_origin,
                    attack_type="direct_return",
                )
            direct_result = _render_attack_result(
                context=f"{context}_first_direct",
                selected_cell=direct_target,
                label="Wie endet der direkte Ball?",
                attack_block_outcome=direct_block_outcome,
            )
            if st.button("Direkten ersten Ball speichern", type="primary", width="stretch"):
                if direct_block_outcome in {"none", "touch_opponent"} and direct_target is None:
                    st.error("Bitte das Zielfeld auswählen.")
                else:
                    pending.update(
                        receiver_id=receiver.id if receiver else "",
                        receiver_name=receiver.name if receiver else "",
                        first_contact_quality=first_quality,
                        first_contact_x=None,
                        first_contact_y=None,
                        first_contact_too_low=first_contact_too_low,
                    )
                    save_pending_action(
                        attacker_id=receiver.id if receiver else "",
                        attacker_name=receiver.name if receiver else "Nicht zugeordnet",
                        attack_type="direct_return",
                        attack_result=direct_result or "continued",
                        attack_block_outcome=direct_block_outcome,
                        attack_origin=direct_origin,
                        landing_x=direct_target[0] if direct_target else None,
                        landing_y=direct_target[1] if direct_target else None,
                        landing_out=landing_cell_is_out(*direct_target) if direct_target else False,
                    )
                    clear_flow()
                    _finish_our_attack(
                        session,
                        state,
                        attacker_name=receiver.name if receiver else "ohne Zuordnung",
                        attack_result=direct_result or "continued",
                        attack_block_outcome=direct_block_outcome,
                    )
            return

        continue_col, skip_col = st.columns(2)
        if continue_col.button(
            "Weiter zum 2. Ball",
            type="primary",
            width="stretch",
            disabled=first_quality is None or first_contact_target is None,
            key=f"{context}_first_continue",
        ):
            advance(
                2,
                receiver_id=receiver.id if receiver else "",
                receiver_name=receiver.name if receiver else "",
                first_contact_quality=first_quality,
                first_contact_x=first_contact_target[0] if first_contact_target else None,
                first_contact_y=first_contact_target[1] if first_contact_target else None,
                first_contact_too_low=first_contact_too_low,
            )
        if skip_col.button("Abnahme überspringen", width="stretch", key=f"{context}_first_skip"):
            advance(
                2,
                receiver_id="",
                receiver_name="",
                first_contact_quality="not_rated",
                first_contact_x=None,
                first_contact_y=None,
                first_contact_too_low=False,
            )
        return

    first_quality = pending.get("first_contact_quality", "not_rated")
    if is_block_recycle:
        st.info("Blockball wieder bei uns · die Annahme wird ausgelassen.")
    elif first_quality in FIRST_CONTACT_LABELS:
        receiver_label = pending.get("receiver_name") or "Team / nicht zugeordnet"
        summary_col, edit_col = st.columns([4, 1])
        summary_col.success(
            f"Abnahme · {receiver_label} · {FIRST_CONTACT_LABELS[first_quality].split(' · ')[0]}"
            + (
                f" · Ziel {int(pending['first_contact_x']) + 1}/{int(pending['first_contact_y']) + 1} m"
                if pending.get("first_contact_x") is not None and pending.get("first_contact_y") is not None
                else ""
            )
            + (" · zu tief" if pending.get("first_contact_too_low") else "")
        )
        if edit_col.button("Ändern", width="stretch", key=f"{context}_edit_first"):
            advance(1)
    else:
        summary_col, edit_col = st.columns([4, 1])
        summary_col.info("Abnahme übersprungen")
        if edit_col.button("Erfassen", width="stretch", key=f"{context}_edit_first_skipped"):
            advance(1)

    if step == 2:
        st.markdown("### 2 · Zweiter Ball")
        if not is_block_recycle and st.button(
            "← Zurück zur Annahme",
            width="stretch",
            key=f"{context}_back_to_first",
        ):
            advance(1)
        st.caption("Erst nach diesem Schritt erscheint der Angriff.")
        default_setter = _default_player(field_players, ("setter", "libero", "outside", "opposite", "middle"))
        passer_choice = st.pills(
            "Wer spielt den zweiten Ball?",
            options=[player.id for player in field_players] + [UNASSIGNED_PLAYER_ID],
            default=default_setter.id,
            format_func=player_choice_label,
            key=f"{context}_passer",
        )
        passer = player_map.get(passer_choice) if passer_choice else None
        is_setter = bool(passer and (passer.primary_position == "setter" or passer.backup_setter))
        has_first_contact_target = (
            pending.get("first_contact_x") is not None and pending.get("first_contact_y") is not None
        )
        set_origin_x = (
            float(pending["first_contact_x"]) + 0.5 if has_first_contact_target else DEFAULT_SET_ORIGIN[0]
        )
        set_origin_y = (
            float(pending["first_contact_y"]) + 0.5 if has_first_contact_target else DEFAULT_SET_ORIGIN[1]
        )
        second_action_options = ["set", "second_ball_return"]
        if is_setter:
            second_action_options.append("setter_tip")
        second_action = st.segmented_control(
            "Was passiert mit dem zweiten Ball?",
            options=second_action_options,
            default="set",
            format_func=lambda value: {
                "set": "Pass",
                "second_ball_return": "Direkt übers Netz",
                "setter_tip": "Zuspielerfinte",
            }[value],
            key=f"{context}_second_action_{passer_choice or 'none'}",
        )
        setter_movement = ""
        if is_setter:
            setter_movement = st.segmented_control(
                "War der Zuspieler rechtzeitig unter dem Ball?",
                options=["", *SETTER_MOVEMENT_LABELS],
                default="",
                format_func=lambda value: "Nicht bewerten" if not value else SETTER_MOVEMENT_LABELS[value],
                key=f"{context}_movement",
            )

        if second_action in {"second_ball_return", "setter_tip"}:
            second_origin = attack_origin_for_player(state, passer.id if passer else "")
            second_block_outcome = _render_attack_block_outcome(context=f"{context}_second_direct")
            second_target = None
            if second_block_outcome in {"none", "touch_opponent"}:
                second_target = _render_landing_picker(
                    context=f"{context}_second_direct",
                    attack_origin=second_origin,
                    attack_type=second_action,
                )
            second_result = _render_attack_result(
                context=f"{context}_second_direct",
                selected_cell=second_target,
                label="Wie endet der zweite Ball?",
                attack_block_outcome=second_block_outcome,
            )
            if st.button("Zweiten Ball speichern", type="primary", width="stretch"):
                if second_block_outcome in {"none", "touch_opponent"} and second_target is None:
                    st.error("Bitte das Zielfeld auswählen.")
                else:
                    pending.update(
                        passer_id=passer.id if passer else "",
                        passer_name=passer.name if passer else "",
                        setter_involved=bool(passer),
                        setter_movement=setter_movement,
                        set_quality="not_rated",
                        set_tendency="not_rated",
                        set_inside_meters=0.0,
                        set_origin="reception_target" if has_first_contact_target else "front",
                        set_origin_x=set_origin_x,
                        set_origin_y=set_origin_y,
                        second_skipped=False,
                    )
                    save_pending_action(
                        attacker_id=passer.id if passer else "",
                        attacker_name=passer.name if passer else "Nicht zugeordnet",
                        attack_type=second_action,
                        attack_result=second_result or "continued",
                        attack_block_outcome=second_block_outcome,
                        attack_origin=second_origin,
                        landing_x=second_target[0] if second_target else None,
                        landing_y=second_target[1] if second_target else None,
                        landing_out=landing_cell_is_out(*second_target) if second_target else False,
                    )
                    clear_flow()
                    _finish_our_attack(
                        session,
                        state,
                        attacker_name=passer.name if passer else "ohne Zuordnung",
                        attack_result=second_result or "continued",
                        attack_block_outcome=second_block_outcome,
                    )
            return

        set_quality = "not_rated"
        set_tendencies: list[str] = []
        set_inside_meters = 0.0
        set_tendency_error: str | None = None
        if is_setter:
            st.caption(
                "Flugbahn und Abstand zum Netz werden getrennt bewertet. "
                "Fehler kann zusammen mit der Ursache gewählt werden und ergibt für die Passform "
                "sofort -1. Ohne Auswahl der Flugbahn wird der Pass nicht bewertet."
            )
            flight_tendencies = (
                st.pills(
                    "Wie liegt die Flugbahn?",
                    options=list(SET_FLIGHT_OPTIONS),
                    default=[],
                    format_func=lambda value: SET_TENDENCY_LABELS[value],
                    selection_mode="multi",
                    key=f"{context}_set_flight_tendencies",
                )
                or []
            )
            net_distance = st.segmented_control(
                "Abstand zum Netz",
                options=["net_good", *SET_NET_DISTANCE_OPTIONS],
                default="net_good",
                format_func=lambda value: (
                    "Gut am Netz" if value == "net_good" else SET_TENDENCY_LABELS[value]
                ),
                key=f"{context}_set_net_distance",
            )
            set_tendencies = list(flight_tendencies)
            if net_distance in SET_NET_DISTANCE_OPTIONS:
                set_tendencies.append(net_distance)
            set_tendency_error = validate_set_tendency_selection(set_tendencies)
            if set_tendency_error:
                st.error(set_tendency_error)
            if "too_far_inside" in set_tendencies:
                set_inside_meters = st.number_input(
                    "Wie viele Meter zu weit innen?",
                    min_value=0.5,
                    max_value=5.0,
                    value=1.0,
                    step=0.5,
                    format="%.1f",
                    key=f"{context}_set_inside_meters",
                )
        else:
            set_quality = st.segmented_control(
                "Wie ist der Pass?",
                options=["not_rated", *PASS_QUALITY_OPTIONS],
                default="not_rated",
                format_func=lambda value: SET_QUALITY_LABELS[value],
                key=f"{context}_set_quality",
            )
        continue_col, skip_col = st.columns(2)
        if continue_col.button("Weiter zum Angriff", type="primary", width="stretch"):
            if set_tendency_error:
                st.error(set_tendency_error)
            else:
                advance(
                    3,
                    passer_id=passer.id if passer else "",
                    passer_name=passer.name if passer else "",
                    setter_involved=bool(passer),
                    setter_movement=setter_movement,
                    set_quality=set_quality,
                    set_tendency=",".join(set_tendencies) if set_tendencies else "not_rated",
                    set_inside_meters=float(set_inside_meters),
                    set_origin="reception_target" if has_first_contact_target else "front",
                    set_origin_x=set_origin_x,
                    set_origin_y=set_origin_y,
                    second_skipped=False,
                )
        if skip_col.button("2. Ball überspringen", width="stretch"):
            advance(
                3,
                passer_id="",
                passer_name="",
                setter_involved=False,
                setter_movement="",
                set_quality="not_rated",
                set_tendency="not_rated",
                set_inside_meters=0.0,
                set_origin="",
                set_origin_x=None,
                set_origin_y=None,
                second_skipped=True,
            )
        return

    if st.button(
        "← Zurück zum 2. Ball",
        width="stretch",
        key=f"{context}_back_to_second",
    ):
        advance(2)

    second_summary_col, second_edit_col = st.columns([4, 1])
    if pending.get("second_skipped"):
        second_summary_col.info("Zweiter Ball übersprungen")
    else:
        passer_label = pending.get("passer_name") or "Team / nicht zugeordnet"
        tendencies = parse_set_tendencies(pending.get("set_tendency"))
        if tendencies:
            quality_label = " + ".join(SET_TENDENCY_LABELS[value] for value in tendencies)
            if "too_far_inside" in tendencies and pending.get("set_inside_meters"):
                quality_label += f" · {float(pending['set_inside_meters']):.1f} m"
        else:
            quality_label = SET_QUALITY_LABELS.get(pending.get("set_quality", "not_rated"), "Nicht bewerten")
        second_summary_col.success(f"2. Ball · {passer_label} · {quality_label}")
    if second_edit_col.button("Ändern", width="stretch", key=f"{context}_edit_second"):
        advance(2)

    st.markdown("### 3 · Angriff")
    passer_id = pending.get("passer_id", "")
    attackers = [player for player in field_players if player.id != passer_id]
    default_attacker = _default_player(attackers, ("outside", "opposite", "middle", "setter", "libero"))
    attacker_choice = st.pills(
        "Wer greift an?",
        options=[player.id for player in attackers] + [UNASSIGNED_PLAYER_ID],
        default=default_attacker.id,
        format_func=player_choice_label,
        key=f"{context}_attacker",
    )
    attacker = player_map.get(attacker_choice) if attacker_choice else None
    attack_type_options = attack_type_options_for_player(
        attacker.id if attacker else "",
        state.get("lineup_roles"),
    )
    attack_type = st.segmented_control(
        "Was macht sie?",
        options=list(attack_type_options),
        default=attack_type_options[0],
        format_func=lambda value: ATTACK_TYPE_LABELS[value],
        key=f"{context}_attack_type_{attacker_choice or 'none'}",
    )
    attack_origin = attack_origin_for_player(state, attacker.id if attacker else "")
    attack_context = f"{context}_{attacker_choice or 'none'}_{attack_type}"
    attack_block_outcome = _render_attack_block_outcome(context=attack_context)
    attack_target = None
    if attack_block_outcome in {"none", "touch_opponent"}:
        attack_target = _render_landing_picker(
            context=attack_context,
            attack_origin=attack_origin,
            attack_type=attack_type,
        )
    attack_result = _render_attack_result(
        context=attack_context,
        selected_cell=attack_target,
        label="Wie endet unser Angriff?",
        attack_block_outcome=attack_block_outcome,
    )

    save_col, skip_col = st.columns(2)
    if save_col.button("Aktion speichern", type="primary", width="stretch"):
        target_required = attack_block_outcome in {"none", "touch_opponent"}
        if (attacker is not None and not attack_origin) or (target_required and attack_target is None):
            st.error("Bitte Angriffsposition und – falls nötig – das Zielfeld auswählen.")
        else:
            save_pending_action(
                attacker_id=attacker.id if attacker else "",
                attacker_name=attacker.name if attacker else "Nicht zugeordnet",
                attack_type=attack_type,
                attack_result=attack_result or "continued",
                attack_block_outcome=attack_block_outcome,
                attack_origin=attack_origin,
                landing_x=attack_target[0] if attack_target else None,
                landing_y=attack_target[1] if attack_target else None,
                landing_out=landing_cell_is_out(*attack_target) if attack_target else False,
            )
            clear_flow()
            _finish_our_attack(
                session,
                state,
                attacker_name=attacker.name if attacker else "ohne Zuordnung",
                attack_result=attack_result or "continued",
                attack_block_outcome=attack_block_outcome,
            )
    if skip_col.button("Angriff überspringen", width="stretch"):
        save_pending_action()
        clear_flow()
        _save_state(session["id"], continue_to_opponent(state))
        st.rerun(scope="app")


def _render_live_session(
    session: dict[str, Any], roster: tuple[Any, ...], player_label: Callable[[Any], str]
) -> None:
    normalized_state = deepcopy(session["state"])
    detected_rotation = setter_rotation_position(
        normalized_state.get("rotation_slots", {}),
        normalized_state.get("lineup_roles"),
    )
    detected_starting_rotation = setter_rotation_position(
        normalized_state.get("starting_rotation_slots", {}),
        normalized_state.get("lineup_roles"),
    )
    if detected_rotation and normalized_state.get("current_rotation") != detected_rotation:
        normalized_state["current_rotation"] = detected_rotation
    if detected_starting_rotation and normalized_state.get("starting_rotation") != detected_starting_rotation:
        normalized_state["starting_rotation"] = detected_starting_rotation
    if normalized_state != session["state"]:
        _save_state(session["id"], normalized_state)
        session = {**session, "state": normalized_state}

    _render_scoreboard(session)
    _render_analysis_video_clip(session)
    _render_lineup_controls(session, roster, player_label)
    state = session["state"]
    show_serve_receive = state.get("phase") == "serve_receive"
    if show_serve_receive:
        contact_context = (
            f"step_live_{session['id']}_{state['current_set']}_{state['rally_number']}_"
            f"{state['sequence_number']}"
        )
        show_serve_receive = int(st.session_state.get(f"{contact_context}_step", 1)) == 1
    _render_live_court(
        state,
        roster,
        show_serve_receive=show_serve_receive,
    )

    latest_rally = state.get("rally_history", [])[-1] if state.get("rally_history") else None
    at_rally_start = int(state.get("sequence_number") or 1) == 1 and state.get("phase") in {
        "serve_receive",
        "opponent_turn",
    }
    can_undo_point = bool(
        latest_rally
        and int(latest_rally.get("set_number") or 0) == int(state.get("current_set") or 0)
        and (at_rally_start or state.get("phase") in {"set_over", "match_over"})
    )
    if st.button(
        "↩ Letzten Punkt rückgängig",
        disabled=not can_undo_point,
        use_container_width=True,
        key=f"undo_last_point_{session['id']}_{state['current_set']}_{state['rally_number']}",
        help=(
            "Setzt Punktestand und Läufer zurück und löscht alle erfassten Aktionen dieses Ballwechsels."
            if can_undo_point
            else "Verfügbar direkt nach einem abgeschlossenen Ballwechsel."
        ),
    ):
        _undo_last_rally_and_refresh(session)

    if state["phase"] not in {"set_over", "match_over"}:
        if st.button(
            "Punkt für Gegner · ohne weitere Angabe",
            use_container_width=True,
            key=f"quick_opponent_point_{session['id']}_{state['current_set']}_{state['rally_number']}",
        ):
            _award_and_refresh(
                session,
                "opponent",
                "Punkt für Gegner",
                result_kind="opponent_point",
            )

    if state["phase"] == "match_over":
        winner = TEAM_NAME if state["our_sets"] > state["opponent_sets"] else session["opponent"]
        st.success(f"Match beendet · {winner} gewinnt {state['our_sets']}:{state['opponent_sets']} Sätze.")
        completed_actions = list_match_actions(session_id=int(session["id"]))
        st.markdown("### Offline sichern")
        _render_json_backup(session, completed_actions, key_suffix="match_end")
        return

    if state["phase"] == "set_over":
        last_set = state["completed_sets"][-1]
        st.success(
            f"Satz {last_set['set_number']} beendet · {last_set['our_score']}:{last_set['opponent_score']}"
        )
        next_set_number = state["current_set"] + 1
        first_set_server = state.get("first_set_server", "opponent")
        default_next_server = (
            first_set_server
            if next_set_number % 2 == 1
            else ("us" if first_set_server == "opponent" else "opponent")
        )
        next_server = st.segmented_control(
            "Wer hat im nächsten Satz zuerst Service?",
            options=["us", "opponent"],
            default=default_next_server,
            format_func=lambda value: "Wir" if value == "us" else "Gegner",
            key=f"next_server_{session['id']}_{state['current_set']}",
        )
        next_rotation = st.segmented_control(
            "Startläufer im nächsten Satz",
            options=[1, 2, 3, 4, 5, 6],
            default=state.get("starting_rotation", 1),
            format_func=lambda value: f"L{value}",
            key=f"next_rotation_{session['id']}_{state['current_set']}",
        )
        player_map = _players_by_id(roster)
        current_lineup = [
            player_map[player_id] for player_id in session["lineup_player_ids"] if player_id in player_map
        ]
        lineup_roles = state.get("lineup_roles") or assign_lineup_roles(current_lineup)
        rotational_ids = set(state.get("rotation_slots", {}).values())
        if not rotational_ids and lineup_roles:
            rotational_ids = {
                lineup_roles["setter"],
                lineup_roles["opposite"],
                *lineup_roles["outsides"],
                *lineup_roles["middles"],
            }
        if not rotational_ids:
            rotational_ids = {player.id for player in current_lineup[:6]}
        rotational_players = [player for player in current_lineup if player.id in rotational_ids]
        next_positions = _render_position_assignment(
            rotational_players,
            next_rotation,
            key_prefix=f"next_set_position_{session['id']}_{next_set_number}",
            lineup_roles=lineup_roles,
        )
        if st.button("Nächsten Satz starten", type="primary", use_container_width=True):
            if len(set(next_positions.values())) != 6:
                st.error("Jeder Spieler darf nur auf einer Startposition stehen.")
            elif lineup_roles and next_positions.get(str(next_rotation)) != lineup_roles["setter"]:
                st.error(f"Bei L{next_rotation} muss der Zuspieler auf Position {next_rotation} stehen.")
            else:
                _save_state(
                    session["id"],
                    start_next_set(
                        state,
                        next_server,
                        starting_rotation=next_rotation,
                        positions=next_positions,
                    ),
                )
                st.rerun()
        return

    if state["phase"] == "opponent_turn":
        _render_opponent_turn(session, roster)
    elif state["phase"] == "block_evaluation":
        _render_block_evaluation(session, roster)
    else:
        _render_our_contact(session, roster)

    if state["rally_history"]:
        st.markdown("#### Letzte Punkte")
        for rally in state["rally_history"][-5:][::-1]:
            winner = TEAM_NAME if rally["winner"] == "us" else session["opponent"]
            st.caption(
                f"Satz {rally['set_number']} · {rally['our_score']}:{rally['opponent_score']} · "
                f"{winner} · {rally['reason']}"
            )


@st.fragment
def _render_analysis(roster: tuple[Any, ...]) -> None:
    with st.expander("Gesichertes Match laden"):
        st.caption(
            "Lade eine zuvor heruntergeladene Herren-1-Matchanalyse-Matchdatei hoch. "
            "Sie wird als neuer Match eingefügt; bestehende Matches bleiben unverändert."
        )
        backup_file = st.file_uploader(
            "Match-Sicherung auswählen",
            type=["json"],
            key="restore_match_backup_file",
        )
        if st.button(
            "Match aus Sicherung einlesen",
            type="primary",
            use_container_width=True,
            disabled=backup_file is None,
            key="restore_match_backup_button",
        ):
            try:
                restored_session_id = restore_match_backup(backup_file.getvalue())
            except (KeyError, TypeError, ValueError) as error:
                st.error(str(error))
            else:
                st.session_state.live_match_session_id = restored_session_id
                st.success("Der Match wurde vollständig eingelesen.")
                st.rerun(scope="app")

    sessions = list_match_sessions()
    if not sessions:
        st.info("Noch kein Match gestartet.")
        return
    sessions = sorted(sessions, key=lambda session: not _is_example_match_name(session["opponent"]))

    selected_session = st.selectbox(
        "Match auswählen",
        options=sessions,
        format_func=_session_label,
        key="live_analysis_session",
    )
    actions = list_match_actions(session_id=int(selected_session["id"]))
    state = selected_session["state"]
    if _is_example_match_name(selected_session["opponent"]):
        st.info(
            "Das ist ein fertiger Beispielmatch mit vier Sätzen. Du kannst hier gefahrlos Filter, "
            "Rotationen und Spielerwerte ausprobieren oder den Match später löschen."
        )
    st.markdown(
        f"**Sätze {state['our_sets']}:{state['opponent_sets']}** · "
        + ("Match beendet" if state["phase"] == "match_over" else f"Satz {state['current_set']} läuft")
    )

    with st.expander("Match sichern", expanded=state.get("phase") == "match_over"):
        _render_json_backup(selected_session, actions, key_suffix="analysis")

    with st.expander("Analyse-PDF für eine Spieler", expanded=False):
        played_players = _played_match_players(selected_session, actions, roster)
        if played_players:
            analysis_player = st.selectbox(
                "Spieler auswählen",
                options=played_players,
                format_func=lambda player: (
                    f"{player.name} · "
                    f"{MATCH_ROLE_LABELS.get(_match_player_role(selected_session, player), player.primary_position)}"
                ),
                key=f"analysis_pdf_player_{selected_session['id']}",
            )
            analysis_role = _match_player_role(selected_session, analysis_player)
            pdf_state_key = (
                f"analysis_pdf_bytes_v{PLAYER_ANALYSIS_PDF_VERSION}_"
                f"{selected_session['id']}_{analysis_player.id}"
            )
            if st.button(
                f"PDF für {analysis_player.name} vorbereiten",
                type="primary",
                use_container_width=True,
                key=f"prepare_analysis_pdf_{selected_session['id']}_{analysis_player.id}",
            ):
                with st.spinner("Spieleranalyse wird erstellt …"):
                    st.session_state[pdf_state_key] = player_analysis_pdf(
                        analysis_player,
                        analysis_role,
                        selected_session,
                        actions,
                    )
            analysis_pdf = st.session_state.get(pdf_state_key)
            if analysis_pdf:
                st.download_button(
                    f"PDF für {analysis_player.name} herunterladen",
                    data=analysis_pdf,
                    file_name=(
                        f"analyse-{_safe_export_name(analysis_player.name.lower())}-"
                        f"{selected_session.get('match_date', 'match')}.pdf"
                    ),
                    mime="application/pdf",
                    use_container_width=True,
                    key=f"download_analysis_pdf_{selected_session['id']}_{analysis_player.id}",
                )
            st.caption(
                "Die PDF zeigt je nach Position andere Schwerpunkte. Bei Zuspieler enthält sie "
                "Passflugbahnen pro Angreifer, Läufer und Position; bei Annahmespieler "
                "getrennte Heatmaps für Serviceannahme, Angriffsabwehr und Gratisball."
            )
        else:
            st.info("Für diesen Match wurden noch keine Spieler gespeichert.")

    video_events = list_match_video_events(session_id=int(selected_session["id"]))
    if video_events:
        st.markdown("### Timeouts und Wechsel im Video")
        st.dataframe(
            _video_event_rows(selected_session, video_events),
            width="stretch",
            hide_index=True,
        )

    available_sets = sorted(
        {state["current_set"]}
        | {item["set_number"] for item in state.get("completed_sets", [])}
        | {action["set_number"] for action in actions}
    )
    with st.expander("Satz löschen"):
        delete_set_number = st.selectbox(
            "Welcher Satz soll gelöscht werden?",
            options=available_sets,
            format_func=lambda value: f"Satz {value}",
            key=f"delete_set_number_{selected_session['id']}",
        )
        restart_server = st.segmented_control(
            "Falls der aktuelle Satz gelöscht wird: Wer serviert beim Neustart?",
            options=["us", "opponent"],
            default="opponent",
            format_func=lambda value: "Wir" if value == "us" else "Gegner",
            key=f"delete_set_server_{selected_session['id']}",
        )
        confirm_delete = st.toggle(
            f"Ja, Satz {delete_set_number} mit allen Aktionen löschen",
            value=False,
            key=f"confirm_delete_set_{selected_session['id']}_{delete_set_number}",
        )
        if st.button(
            f"Satz {delete_set_number} endgültig löschen",
            disabled=not confirm_delete,
            use_container_width=True,
            key=f"delete_set_button_{selected_session['id']}_{delete_set_number}",
        ):
            delete_match_set_actions(
                session_id=selected_session["id"],
                set_number=delete_set_number,
            )
            updated_state = delete_set_from_state(
                state,
                delete_set_number,
                restart_server=restart_server,
            )
            _save_state(selected_session["id"], updated_state)
            st.session_state.live_match_session_id = selected_session["id"]
            st.rerun()

    with st.expander("Ganzes Match löschen"):
        st.warning(
            f"Dabei werden {selected_session['opponent']}, alle Sätze und alle erfassten Aktionen gelöscht."
        )
        confirm_match_delete = st.toggle(
            "Ja, dieses Match vollständig löschen",
            value=False,
            key=f"confirm_match_delete_{selected_session['id']}",
        )
        if st.button(
            "Match endgültig löschen",
            disabled=not confirm_match_delete,
            use_container_width=True,
            key=f"delete_match_{selected_session['id']}",
        ):
            delete_match_session(session_id=selected_session["id"])
            if st.session_state.get("live_match_session_id") == selected_session["id"]:
                st.session_state.pop("live_match_session_id", None)
            st.rerun()

    substitutions = state.get("substitutions", [])
    if substitutions:
        st.markdown("### Spielerwechsel")
        substitution_rows = [
            {
                "Satz": item["set_number"],
                "Spielstand": f"{item['our_score']}:{item['opponent_score']}",
                "Läufer": f"L{item['rotation']}",
                "Raus": item["outgoing_name"],
                "Rein": item["incoming_name"],
                "Rolle": MATCH_ROLE_LABELS.get(item.get("role"), item.get("role", "")),
                "Ballwechsel": item["rally_number"],
            }
            for item in substitutions
        ]
        st.dataframe(substitution_rows, use_container_width=True, hide_index=True)

    rally_history = state.get("rally_history", [])
    if rally_history:
        st.markdown("### Rotationen")
        rotation_rows = []
        for rotation in range(1, 7):
            rallies = [item for item in rally_history if item.get("rotation", 1) == rotation]
            won = sum(1 for item in rallies if item["winner"] == "us")
            lost = sum(1 for item in rallies if item["winner"] == "opponent")
            own_errors = sum(1 for item in rallies if item.get("result_kind") == "our_error")
            rotation_rows.append(
                {
                    "Läufer": f"L{rotation}",
                    "Ballwechsel": len(rallies),
                    "Punkte gewonnen": won,
                    "Punkte verloren": lost,
                    "Eigene Fehler": own_errors,
                    "Punktquote": f"{won / len(rallies):.0%}" if rallies else "–",
                    "Bilanz": won - lost,
                }
            )
        st.dataframe(rotation_rows, use_container_width=True, hide_index=True)

    if not actions:
        st.info("Für dieses Match wurden noch keine eigenen Ballkontakte gespeichert.")
        return

    filter_set, filter_type = st.columns(2)
    selected_set = filter_set.selectbox(
        "Satz",
        options=["all"] + sorted({action["set_number"] for action in actions}),
        format_func=lambda value: "Ganzes Match" if value == "all" else f"Satz {value}",
        key="live_analysis_set",
    )
    selected_type = filter_type.selectbox(
        "Ballart",
        options=["all"] + sorted({action["ball_type"] for action in actions}),
        format_func=lambda value: "Alle Ballarten" if value == "all" else BALL_TYPE_LABELS[value],
        key="live_analysis_type",
    )
    filtered = [
        action
        for action in actions
        if (selected_set == "all" or action["set_number"] == selected_set)
        and (selected_type == "all" or action["ball_type"] == selected_type)
    ]
    summary = summarize_match_actions(filtered)
    selected_set_number = None if selected_set == "all" else int(selected_set)
    phase_efficiency = summarize_phase_efficiency(
        state,
        actions,
        set_number=selected_set_number,
    )
    training_actions = [
        action
        for action in actions
        if selected_set_number is None or int(action["set_number"]) == selected_set_number
    ]
    training_summary = summarize_match_actions(training_actions)

    total_services = sum(row["total"] for row in summary["services"].values())
    total_attacks = sum(row["total"] for row in summary["attacks"].values())
    total_points = sum(row["point"] for row in summary["attacks"].values())
    metric_a, metric_b, metric_c, metric_d = st.columns(4)
    metric_a.metric("Annahmen / Abwehren", summary["total_receptions"])
    metric_b.metric("Eigene Services", total_services)
    metric_c.metric("Angriffsaktionen (3. Ball)", total_attacks)
    metric_d.metric("Direkte Angriffspunkte", total_points)

    def _render_overview_section() -> None:
        st.markdown("### Sideout und Breakpoint")
        phase = phase_efficiency["overall"]
        phase_a, phase_b, phase_c = st.columns(3)
        phase_a.metric(
            "Sideout",
            rate_with_counts(phase["sideout_won"], phase["sideout_attempts"]),
        )
        phase_b.metric(
            "First-Ball-Sideout",
            rate_with_counts(
                phase["first_ball_sideout_won"],
                phase["first_ball_sideout_attempts"],
            ),
        )
        phase_c.metric(
            "Breakpoint",
            rate_with_counts(phase["breakpoint_won"], phase["breakpoint_attempts"]),
        )
        st.caption(
            "Sideout = Punktgewinn bei gegnerischem Service · First-Ball-Sideout = direkter Punkt "
            "mit unserem ersten Angriff nach dem Service; übersprungene erste Bälle zählen dort nicht · "
            "Breakpoint = Punktgewinn bei eigenem Service. "
            "Diese Quoten folgen dem Satzfilter, nicht dem Ballartfilter."
        )

        phase_rotation_rows = []
        for rotation in range(1, 7):
            values = phase_efficiency["rotations"].get(
                rotation,
                {
                    "sideout_attempts": 0,
                    "sideout_won": 0,
                    "first_ball_sideout_attempts": 0,
                    "first_ball_sideout_won": 0,
                    "breakpoint_attempts": 0,
                    "breakpoint_won": 0,
                },
            )
            phase_rotation_rows.append(
                {
                    "Läufer": f"L{rotation}",
                    "Sideout": rate_with_counts(values["sideout_won"], values["sideout_attempts"]),
                    "First-Ball-Sideout": rate_with_counts(
                        values["first_ball_sideout_won"],
                        values["first_ball_sideout_attempts"],
                    ),
                    "Breakpoint": rate_with_counts(values["breakpoint_won"], values["breakpoint_attempts"]),
                }
            )
        st.dataframe(phase_rotation_rows, use_container_width=True, hide_index=True)

        if phase_efficiency["servers"]:
            with st.expander("Breakpoint nach Aufschläger"):
                server_rows = []
                for name, values in sorted(
                    phase_efficiency["servers"].items(),
                    key=lambda item: (-item[1]["attempts"], item[0]),
                ):
                    server_rows.append(
                        {
                            "Spieler": name,
                            "Servicephasen": values["attempts"],
                            "Breakpoints": rate_with_counts(values["won"], values["attempts"]),
                            "Asse": values["aces"],
                            "Servicefehler": values["errors"],
                        }
                    )
                st.dataframe(server_rows, use_container_width=True, hide_index=True)

        st.markdown("### Vorschläge für das nächste Training")
        training_recommendations = recommend_training_focuses(
            training_summary,
            phase_efficiency,
        )
        if training_recommendations:
            exercise_library = {exercise.id: exercise for exercise in default_exercises()}
            st.caption(
                "Die Vorschläge verwenden nur Bereiche mit mindestens fünf erfassten Aktionen. "
                "Sie folgen dem Satzfilter und werden direkt aus den Matchwerten begründet."
            )
            for index, recommendation in enumerate(training_recommendations):
                with st.expander(recommendation["title"], expanded=index == 0):
                    st.markdown("**Warum:** " + " · ".join(recommendation["reasons"]))
                    for exercise_id in TRAINING_EXERCISE_IDS.get(recommendation["focus"], ()):
                        exercise = exercise_library.get(exercise_id)
                        if exercise is None:
                            continue
                        st.markdown(f"**{exercise.title}**")
                        st.write(exercise.goal)
                        st.caption(exercise.setup)
        else:
            st.info(
                "Noch keine klare Schwäche mit genügend Daten. Nach mindestens fünf passenden "
                "Ballwechseln erscheinen hier begründete Übungen."
            )

        st.markdown("### Leistungsverlauf")
        performance_mode = st.segmented_control(
            "Verlauf anzeigen für",
            options=["match", "set"],
            default="match" if selected_set == "all" else "set",
            format_func=lambda value: "Ganzes Match" if value == "match" else "Einzelner Satz",
            key=f"performance_mode_{selected_session['id']}_{selected_set}_{selected_type}",
        )
        performance_source = list(actions)
        performance_set: int | None = None
        if performance_mode == "set":
            performance_sets = sorted({int(action["set_number"]) for action in actions})
            default_performance_set = (
                int(selected_set)
                if selected_set != "all" and int(selected_set) in performance_sets
                else performance_sets[0]
            )
            performance_set = st.selectbox(
                "Satz für den Leistungsverlauf",
                options=performance_sets,
                index=performance_sets.index(default_performance_set),
                format_func=lambda value: f"Satz {value}",
                key=(f"performance_set_{selected_session['id']}_{selected_set}_{selected_type}"),
            )
        performance_history = list(state.get("rally_history", []))
        performance = build_player_point_performance(
            performance_source,
            performance_history,
            reset_each_set=performance_mode == "set",
            lineup_roles=state.get("lineup_roles"),
            substitutions=state.get("substitutions", []),
            player_names_by_id={player.id: player.name for player in roster},
        )
        if performance_mode == "set" and performance_set is not None:
            filtered_performance: dict[str, dict[str, list[dict[str, Any]]]] = {}
            for player_name, metrics in performance.items():
                selected_metrics: dict[str, list[dict[str, Any]]] = {}
                for metric, events in metrics.items():
                    selected_events = [
                        event for event in events if int(event.get("set_number") or 1) == performance_set
                    ]
                    if selected_events and any(event.get("had_action") for event in selected_events):
                        selected_metrics[metric] = selected_events
                if selected_metrics:
                    filtered_performance[player_name] = selected_metrics
            performance = filtered_performance
        if performance:
            if performance_mode == "match":
                st.caption(
                    "Matchansicht: Die Skala startet einmal in der mittleren gelben Stufe und läuft "
                    "über Satzwechsel sowie Ein- und Auswechslungen weiter. Satzgrenzen sind schwarz "
                    "markiert. Jeder Farbbalken umfasst alle Punkte des Matches."
                )
            else:
                st.caption(
                    f"Satzansicht: Satz {performance_set} beginnt für jeden Spieler und jede Kategorie "
                    "neu in der mittleren gelben Stufe. Gute Aktionen führen Richtung Grün, negative "
                    "Richtung Rot. Der Balken zeigt den gesamten Satz auf einer 25-Punkte-Skala; "
                    "im fünften Satz sind es 15 Punkte."
                )
            performance_player = st.selectbox(
                "Spieler für den Leistungsverlauf",
                options=sorted(performance),
                key=(
                    f"performance_player_{selected_session['id']}_{performance_mode}_"
                    f"{performance_set}_{selected_type}"
                ),
            )
            performance_player_object = next(
                (player for player in roster if player.name == performance_player),
                None,
            )
            performance_role = (
                _match_player_role(selected_session, performance_player_object)
                if performance_player_object is not None
                else ""
            )
            player_performance = performance_metrics_for_role(
                performance[performance_player],
                performance_role,
            )
            st.markdown(
                performance_timeline_html(performance_player, player_performance),
                unsafe_allow_html=True,
            )
            st.caption("Die genaue Aktion wird beim Antippen oder Darüberfahren angezeigt.")
        else:
            st.info("Für diese Auswahl gibt es noch keine bewerteten Spieleraktionen.")

    def _render_first_contact_section() -> None:
        st.markdown("### Service")
        if summary["services"]:
            service_rows = []
            for name, values in sorted(
                summary["services"].items(), key=lambda item: (-item[1]["total"], item[0])
            ):
                service_rows.append(
                    {
                        "Spieler": name,
                        "Services": values["total"],
                        "Jump": rate_with_counts(values["jump"], values["total"]),
                        "Aus dem Stand": rate_with_counts(values["standing"], values["total"]),
                        "Ass": rate_with_counts(values["ace"], values["total"]),
                        "Sehr gut": rate_with_counts(values["very_good"], values["total"]),
                        "Gut": rate_with_counts(values["good"], values["total"]),
                        "Okay": rate_with_counts(values["okay"], values["total"]),
                        "Fehler": rate_with_counts(values["error"], values["total"]),
                    }
                )
            st.dataframe(service_rows, use_container_width=True, hide_index=True)
        else:
            st.info("Noch keine eigenen Services in dieser Auswahl.")

        service_trajectory_actions = [
            action
            for action in filtered
            if action.get("ball_type") == "service"
            and action.get("server_id")
            and action.get("service_origin_x") is not None
            and action.get("landing_x") is not None
            and action.get("landing_y") is not None
        ]
        if service_trajectory_actions:
            st.markdown("#### Servicerichtungen")
            st.caption(
                "Ass = Grün · sehr guter Service = Gelb · guter oder Okay-Service = Schwarz · "
                "Fehler = Rot. Die Zahl am Start zeigt den 1-m-Abschnitt hinter der Grundlinie."
            )
            service_player_names = {
                action["server_id"]: action["server_name"] for action in service_trajectory_actions
            }
            service_player_id = st.selectbox(
                "Spieler für die Servicekarte",
                options=sorted(
                    service_player_names,
                    key=lambda player_id: service_player_names[player_id],
                ),
                format_func=lambda player_id: service_player_names[player_id],
                key=f"service_trajectory_player_{selected_session['id']}_{selected_set}_{selected_type}",
            )
            st.markdown(
                service_trajectory_svg(
                    service_player_names[service_player_id],
                    [
                        action
                        for action in service_trajectory_actions
                        if action["server_id"] == service_player_id
                    ],
                ),
                unsafe_allow_html=True,
            )
        elif summary["services"]:
            st.info(
                "Für ältere Services fehlen noch Serviceort oder Zielfeld. Neu erfasste Services "
                "erscheinen hier automatisch als Pfeile."
            )
        st.markdown("### Annahme und Abwehr")
        reception_rows = []
        for name, values in sorted(
            summary["receptions"].items(), key=lambda item: (-item[1]["total"], item[0])
        ):
            positive = values["perfect"] + values["good"]
            reception_rows.append(
                {
                    "Spieler": name,
                    "Bälle": values["total"],
                    "Perfekt": values["perfect"],
                    "Gut": values["good"],
                    "Okay": values["okay"],
                    "Schlecht": values["bad"],
                    "Annahmefehler": values["error"],
                    "Zu tief": values["too_low"],
                    "Perfekt/Gut": f"{positive / values['total']:.0%}",
                }
            )
        if reception_rows:
            st.dataframe(reception_rows, use_container_width=True, hide_index=True)
        else:
            st.info("Keine Annahmen oder Abwehren in dieser Auswahl.")

        no_contacts = summary.get("no_contacts", {})
        if no_contacts.get("total"):
            st.markdown("#### Kein erster Kontakt")
            st.caption(
                "Diese Bälle wurden von keiner Spieler berührt und zählen deshalb nicht als "
                "individueller Annahmefehler."
            )
            no_contact_rows = []
            for no_contact_ball_type, values in sorted(
                no_contacts.get("by_ball_type", {}).items(),
                key=lambda item: BALL_TYPE_LABELS.get(item[0], item[0]),
            ):
                no_contact_rows.append(
                    {
                        "Ballart": BALL_TYPE_LABELS.get(
                            no_contact_ball_type,
                            no_contact_ball_type,
                        ),
                        "Fälle": values["total"],
                        "Guter gegnerischer Ball": values["quality"],
                        "Kommunikation": values["communication"],
                    }
                )
            st.dataframe(no_contact_rows, use_container_width=True, hide_index=True)

            communication_groups = no_contacts.get("communication_groups", {})
            if communication_groups:
                communication_rows = [
                    {
                        "Spieler": names,
                        "Kommunikationsfehler": count,
                    }
                    for names, count in sorted(
                        communication_groups.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ]
                st.markdown("##### Kommunikationsfehler zwischen")
                st.dataframe(
                    communication_rows,
                    use_container_width=True,
                    hide_index=True,
                )

        reception_target_actions = [
            action
            for action in filtered
            if action.get("first_contact_x") is not None and action.get("first_contact_y") is not None
        ]
        st.markdown("#### Annahmeziele als Heatmap")
        st.caption("Dunklere Felder bedeuten mehr Annahmen. Hohe und zu tiefe Annahmen zählen hier gleich.")
        if reception_target_actions:
            team_heatmap_tab, player_heatmap_tab = st.tabs(["Ganzes Team", "Pro Spieler"])
            with team_heatmap_tab:
                st.markdown(
                    reception_heatmap_svg("Ganzes Team", reception_target_actions),
                    unsafe_allow_html=True,
                )
            with player_heatmap_tab:
                receiver_names = {
                    action.get("receiver_id") or f"name:{action.get('receiver_name')}": (
                        action.get("receiver_name") or "Nicht zugeordnet"
                    )
                    for action in reception_target_actions
                    if action.get("receiver_id") or action.get("receiver_name")
                }
                if receiver_names:
                    selected_receiver_key = st.selectbox(
                        "Spieler",
                        options=sorted(receiver_names, key=lambda key: receiver_names[key]),
                        format_func=lambda key: receiver_names[key],
                        key=(
                            f"reception_heatmap_player_{selected_session['id']}_"
                            f"{selected_set}_{selected_type}"
                        ),
                    )
                    player_actions = [
                        action
                        for action in reception_target_actions
                        if (action.get("receiver_id") or f"name:{action.get('receiver_name')}")
                        == selected_receiver_key
                    ]
                    st.markdown(
                        reception_heatmap_svg(receiver_names[selected_receiver_key], player_actions),
                        unsafe_allow_html=True,
                    )
                else:
                    st.info("Für die gespeicherten Annahmeziele ist keine Spieler zugeordnet.")
        else:
            st.info("Noch keine Annahmeziele in dieser Auswahl gespeichert.")

    def _render_block_section() -> None:
        st.markdown("### Block")
        if summary["blocks"]:
            block_rows = []
            block_origin_rows = []
            for name, values in sorted(
                summary["blocks"].items(), key=lambda item: (-item[1]["total"], item[0])
            ):
                block_rows.append(
                    {
                        "Spieler": name,
                        "Blockaktionen": values["total"],
                        "Kein Touch": values["no_touch"],
                        "Blocktouch": values["touch"],
                        "Blockpunkt": values["point"],
                        "Blockfehler": values["error"],
                        "Block war zu": values["closed"],
                        "Mitte zu langsam": values["middle_late"],
                        "Kein Block nötig": values["not_needed"],
                        "Blockpunktquote": f"{values['point'] / values['total']:.0%}"
                        if values["total"]
                        else "–",
                    }
                )
                for origin, origin_values in values.get("opponent_origins", {}).items():
                    block_origin_rows.append(
                        {
                            "Spieler": name,
                            "Gegnerischer Angriff": OPPONENT_ATTACK_ORIGIN_LABELS[origin],
                            "Blockaktionen": origin_values["total"],
                            "Block war zu": origin_values["closed"],
                            "Blocktouch": origin_values["touch"],
                            "Blockpunkt": origin_values["point"],
                            "Blockfehler": origin_values["error"],
                            "Mitte zu langsam": origin_values["middle_late"],
                            "Blockpunktquote": rate_with_counts(
                                origin_values["point"], origin_values["total"]
                            ),
                        }
                    )
            st.dataframe(block_rows, use_container_width=True, hide_index=True)
            if block_origin_rows:
                st.markdown("#### Block nach gegnerischer Angriffsposition")
                st.caption(
                    "So siehst du besonders bei den Mitten, gegen welche Angriffsposition der Block "
                    "funktioniert und wo er zu spät oder fehlerhaft ist."
                )
                st.dataframe(block_origin_rows, use_container_width=True, hide_index=True)
        else:
            st.info("Noch keine Blockaktionen in dieser Auswahl.")

    def _render_attack_section() -> None:
        st.markdown("### Angriff")
        if summary["attacks"]:
            st.caption(
                "Gezählt werden nur dritte Bälle: Schlag, Finte und Safe Ball. Als optimale Passgruppe "
                "gelten optimale und zu hohe Pässe sowie Pässe bis 1,0 m zu weit innen, sofern keine "
                "weitere ungünstige Abweichung gewählt wurde. Klammern zeigen Punkte / Versuche; bei "
                "der Fehlerquote Fehler / Angriffsaktionen."
            )

            attack_rows = []
            for name, values in sorted(
                summary["attacks"].items(), key=lambda item: (-item[1]["total"], item[0])
            ):
                attack_rows.append(
                    {
                        "Spieler": name,
                        "Angriffsaktionen (3. Ball)": values["total"],
                        "Angreifbare Bälle verwertet · optimale Pässe": rate_with_counts(
                            values["optimal_pass_point"], values["optimal_pass_total"]
                        ),
                        "Angreifbare Bälle verwertet · andere Pässe": rate_with_counts(
                            values["other_pass_point"], values["other_pass_total"]
                        ),
                        "Schlag": rate_with_counts(values["spike_point"], values["spike"]),
                        "Finte": rate_with_counts(values["tip_point"], values["tip"]),
                        "Safe Ball": rate_with_counts(values["safe_point"], values["safe"]),
                        "Fehlerquote": rate_with_counts(values["error"], values["total"]),
                    }
                )
            st.dataframe(attack_rows, use_container_width=True, hide_index=True)
        else:
            st.info("Noch keine eigenen Angriffe in dieser Auswahl.")

        trajectory_actions = [
            action
            for action in filtered
            if action.get("attacker_id")
            and action.get("attack_origin")
            and (
                (action.get("landing_x") is not None and action.get("landing_y") is not None)
                or action.get("attack_block_outcome") in {"blockout", "recycle_us", "blocked_point"}
            )
            and action.get("attack_type") in THIRD_BALL_ATTACK_TYPES
        ]
        if trajectory_actions:
            st.markdown("### Angriffsrichtungen")
            st.caption(
                "Grün = Punkt · Schwarz = Ballwechsel geht weiter · Rot = Fehler · "
                "jede Linie beginnt an der beim Angriff gespeicherten Systemposition"
            )
            trajectory_player_names = {
                action["attacker_id"]: action["attacker_name"] for action in trajectory_actions
            }
            trajectory_player_id = st.selectbox(
                "Spieler für die drei Schlagkarten",
                options=sorted(
                    trajectory_player_names, key=lambda player_id: trajectory_player_names[player_id]
                ),
                format_func=lambda player_id: trajectory_player_names[player_id],
                key=f"trajectory_player_{selected_session['id']}_{selected_set}_{selected_type}",
            )
            player_actions = [
                action for action in trajectory_actions if action["attacker_id"] == trajectory_player_id
            ]
            map_definitions = (
                ("Schlag", {"spike"}),
                ("Finte", {"tip"}),
                ("Safe Ball", {"safe"}),
            )
            map_columns = st.columns(3)
            for column, (title, attack_types) in zip(map_columns, map_definitions):
                map_actions = [
                    action for action in player_actions if action.get("attack_type") in attack_types
                ]
                column.markdown(
                    attack_trajectory_svg(
                        trajectory_player_names[trajectory_player_id],
                        title,
                        map_actions,
                    ),
                    unsafe_allow_html=True,
                )
        elif summary["attacks"]:
            st.info(
                "Für diese Auswahl wurden noch keine Zielfelder gespeichert. Neue Aktionen erscheinen hier automatisch."
            )

    def _render_setting_section() -> None:
        st.markdown("### Zweiter Ball")
        if summary["setters"]:
            setter_rows = []
            for name, values in sorted(
                summary["setters"].items(), key=lambda item: (-item[1]["total"], item[0])
            ):
                setter_rows.append(
                    {
                        "Spieler": name,
                        "Zweite Bälle": values["total"],
                        "Schnell am Ball": values["fast"],
                        "Zu spät / faul": values["late"],
                        "Optimal": values["optimal"],
                        "Zu tief": values["too_low"],
                        "Zu hoch": values["too_high"],
                        "Zu weit aussen": values["too_far_outside"],
                        "Zu weit innen": values["too_far_inside"],
                        "Zu nahe am Netz": values["too_close_net"],
                        "Zu weit weg vom Netz": values["too_far_net"],
                        "Zuspielerfinten": values["setter_tip"],
                        "Punkte mit Zuspielerfinte": values["setter_tip_point"],
                        "Zuspielerfinte Punktquote": (
                            f"{values['setter_tip_point'] / values['setter_tip']:.0%}"
                            if values["setter_tip"]
                            else "–"
                        ),
                    }
                )
            st.dataframe(setter_rows, use_container_width=True, hide_index=True)

            historical_rows = []
            for name, values in sorted(summary["setters"].items()):
                historical_total = sum(
                    values[key] for key in ("very_good", "good", "okay", "playable", "bad", "not_good")
                )
                if not historical_total:
                    continue
                historical_rows.append(
                    {
                        "Spieler": name,
                        "Sehr gut": values["very_good"],
                        "Gut": values["good"],
                        "Okay": values["okay"],
                        "Spielbar": values["playable"],
                        "Schlecht": values["bad"] + values["not_good"],
                    }
                )
            if historical_rows:
                with st.expander("Frühere Passbewertungen anzeigen"):
                    st.caption(
                        "Diese Einträge stammen aus Matches vor dem neuen technischen Bewertungsschema."
                    )
                    st.dataframe(historical_rows, use_container_width=True, hide_index=True)

        pass_actions = [
            action
            for action in filtered
            if action.get("setter_id")
            and action.get("set_origin_x") is not None
            and action.get("set_origin_y") is not None
            and action.get("attack_origin") in PASS_ATTACK_TARGETS_3D
            and pass_trajectory_color_tendencies(action.get("set_tendency"))
            and action.get("attack_type") not in {"setter_tip", "second_ball_return"}
        ]
        st.markdown("#### Passflugbahnen in 3D-Perspektive")
        st.caption(
            "🟢 Optimal · 🔴 Zu tief · 🟠 Zu hoch · 🟡 Zu weit innen/aussen, sofern keine Höhenabweichung gewählt ist. "
            "Der Abstand zum Netz verändert die Flugbahn nicht und steht separat in der Statistik."
        )
        if pass_actions:
            setter_names = {
                action["setter_id"]: action.get("setter_name") or "Zuspieler" for action in pass_actions
            }
            selected_setter_id = st.selectbox(
                "Zuspieler",
                options=sorted(setter_names, key=lambda setter_id: setter_names[setter_id]),
                format_func=lambda setter_id: setter_names[setter_id],
                key=f"pass_arc_setter_{selected_session['id']}_{selected_set}_{selected_type}",
            )
            setter_passes = [action for action in pass_actions if action["setter_id"] == selected_setter_id]
            group_mode = st.segmented_control(
                "Passbögen anzeigen nach",
                options=["player", "position"],
                default="player",
                format_func=lambda value: "Angreifer" if value == "player" else "Angriffsposition",
                key=f"pass_arc_mode_{selected_session['id']}_{selected_set}_{selected_type}",
            )
            if group_mode == "position":
                positions = sorted(
                    {action["attack_origin"] for action in setter_passes},
                    key=lambda position: ("4", "3", "2", "6", "1", "5").index(position),
                )
                selected_position = st.selectbox(
                    "Angriffsposition",
                    options=positions,
                    format_func=lambda position: ATTACK_ORIGIN_LABELS.get(position, f"P{position}"),
                    key=f"pass_arc_position_{selected_session['id']}_{selected_set}_{selected_type}",
                )
                displayed_passes = [
                    action for action in setter_passes if action["attack_origin"] == selected_position
                ]
                pass_title = (
                    f"{setter_names[selected_setter_id]} → "
                    f"{ATTACK_ORIGIN_LABELS.get(selected_position, f'P{selected_position}')}"
                )
            else:
                target_names = {
                    action["attacker_id"]: action.get("attacker_name") or "Angreifer"
                    for action in setter_passes
                }
                selected_target_id = st.selectbox(
                    "Angreifer",
                    options=sorted(target_names, key=lambda player_id: target_names[player_id]),
                    format_func=lambda player_id: target_names[player_id],
                    key=f"pass_arc_player_{selected_session['id']}_{selected_set}_{selected_type}",
                )
                displayed_passes = [
                    action for action in setter_passes if action["attacker_id"] == selected_target_id
                ]
                pass_title = f"{setter_names[selected_setter_id]} → {target_names[selected_target_id]}"

            st.markdown(pass_trajectory_svg(pass_title, displayed_passes), unsafe_allow_html=True)
            st.caption(
                "Rosa Ball = zusammengefasster 2×2-m-Zuspielbereich aus dem genauen Annahmeziel · "
                "die Annahme selbst bleibt auf 1×1 m genau · weisser Positionskreis = ideale Angriffshöhe, "
                "Pfeilspitze = tatsächliche Passhöhe. "
                "Die schräge Ansicht zeigt Tiefe, seitliche Richtung und Ballhöhe; Innenpässe verschieben das Ziel um die erfassten Meter. "
                "Zu nahe oder zu weit weg vom Netz wird hier bewusst als gute Netzdistance gezeichnet."
            )
        else:
            st.info(
                "Für diese Auswahl gibt es noch keine Pässe mit gespeichertem Annahmeziel. "
                "Neue Ballwechsel erscheinen hier automatisch."
            )

        st.markdown("#### Passlage pro Angreifer")
        st.caption(
            "Legende: 🔴 Fehler · 🔵 Zu tief · 🟢 Optimal · 🟠 Zu hoch · "
            "Türkis = zu weit innen · 🟡 Zu weit aussen · "
            "Netzabstand separat · Kombinationen zählen in mehreren Spalten"
        )
        setter_targets = summary.get("setter_targets", {})
        target_rows = []

        def count_with_share(count: int, total: int) -> str:
            return f"{count} · {count / total:.0%}" if total else "0 · 0%"

        for setter_name, targets in sorted(setter_targets.items()):
            for target_name, values in sorted(targets.items(), key=lambda item: (-item[1]["total"], item[0])):
                total = values["total"]
                inside_average = (
                    values["inside_meters_total"] / values["inside_meters_count"]
                    if values["inside_meters_count"]
                    else None
                )
                tendency_counts = {
                    key: values[key]
                    for key in (
                        "error",
                        "optimal",
                        "too_low",
                        "too_high",
                        "too_far_outside",
                        "too_far_inside",
                        "too_close_net",
                        "too_far_net",
                    )
                }
                most_common_key = max(tendency_counts, key=tendency_counts.get)
                target_rows.append(
                    {
                        "Zuspieler": setter_name,
                        "Angreifer": target_name,
                        "Pässe": total,
                        "🔴 Fehler": count_with_share(values["error"], total),
                        "🟢 Optimal": count_with_share(values["optimal"], total),
                        "🔴 Zu tief": count_with_share(values["too_low"], total),
                        "🟠 Zu hoch": count_with_share(values["too_high"], total),
                        "🟡 Zu weit aussen": count_with_share(values["too_far_outside"], total),
                        "🟡 Zu weit innen": count_with_share(values["too_far_inside"], total),
                        "Zu nahe am Netz": count_with_share(values["too_close_net"], total),
                        "Zu weit weg vom Netz": count_with_share(values["too_far_net"], total),
                        "Ø innen": f"{inside_average:.1f} m" if inside_average is not None else "–",
                        "Häufigste Lage": SET_TENDENCY_LABELS[most_common_key],
                    }
                )
        if target_rows:
            st.dataframe(target_rows, use_container_width=True, hide_index=True)
        else:
            st.info(
                "Noch keine technische Passlage mit Zielspielerin erfasst. "
                "Sie erscheint hier nach dem nächsten bewerteten Pass."
            )

    analysis_section = st.segmented_control(
        "Analysebereich",
        ["Überblick", "Service & Annahme", "Block", "Angriff", "Zuspiel"],
        default="Überblick",
        key=f"analysis_section_{selected_session['id']}",
        width="stretch",
    )
    section_renderers = {
        "Überblick": _render_overview_section,
        "Service & Annahme": _render_first_contact_section,
        "Block": _render_block_section,
        "Angriff": _render_attack_section,
        "Zuspiel": _render_setting_section,
    }
    section_renderers.get(analysis_section or "Überblick", _render_overview_section)()


def render_match_analysis(roster: tuple[Any, ...], player_label: Callable[[Any], str]) -> None:
    ensure_demo_match(roster)
    live_tab, cut_tab, analysis_tab = st.tabs(["Punkte analysieren", "Video schneiden", "Analyse ansehen"])
    session_id = st.session_state.get("live_match_session_id")
    session = get_match_session(session_id) if session_id else None
    if session is None:
        st.session_state.pop("live_match_session_id", None)
    with cut_tab:
        if session is None:
            _render_video_project_setup()
        else:
            _render_video_cutter(session, roster)
    with live_tab:
        if session is None:
            st.info(
                "Der fertige **Beispielmatch** befindet sich im Tab "
                "**Analyse ansehen**. Hier kannst du einen eigenen Match beginnen."
            )
            _render_match_setup(roster, player_label)
        elif not _session_analysis_ready(session):
            _render_existing_video_analysis_setup(session, roster, player_label)
        else:
            _render_live_session(session, roster, player_label)
    with analysis_tab:
        _render_analysis(roster)
