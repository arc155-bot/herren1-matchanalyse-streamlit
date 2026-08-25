from __future__ import annotations

from collections import Counter
from io import BytesIO
from math import atan2, cos, pi, sin
from typing import Any, Callable, Iterable

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfgen import canvas

from match_analysis import (
    ATTACK_TYPE_LABELS,
    FIRST_CONTACT_LABELS,
    PERFORMANCE_BENCH_COLOR,
    OPPONENT_ATTACK_ORIGIN_LABELS,
    PERFORMANCE_LEVEL_COLORS,
    PERFORMANCE_METRIC_LABELS,
    SET_TENDENCY_LABELS,
    THIRD_BALL_ATTACK_TYPES,
    build_player_point_performance,
    parse_set_tendencies,
    summarize_match_actions,
)
from volleyball_net import (
    WOMENS_NET_BOTTOM_METERS,
    WOMENS_NET_TOP_METERS,
    net_mesh_heights,
    net_mesh_positions,
)


PINK = colors.HexColor("#F6B4CD")
PURPLE = colors.HexColor("#7561A8")
INK = colors.HexColor("#111014")
MUTED = colors.HexColor("#6f6875")
PALE = colors.HexColor("#fff0f6")
LINE = colors.HexColor("#ded8e1")
GREEN = colors.HexColor("#15803d")
RED = colors.HexColor("#dc2626")
ORANGE = colors.HexColor("#f97316")
YELLOW = colors.HexColor("#eab308")
BLUE = colors.HexColor("#2563eb")
TURQUOISE = colors.HexColor("#0891b2")
LANDSCAPE_A4 = landscape(A4)
ROLE_LABELS = {
    "setter": "Zuspieler",
    "opposite": "Dia",
    "outside": "Aussen",
    "middle": "Mitte",
    "libero": "Libero",
}
LIBERO_RECEPTION_POSITIONS = {1: "P6", 6: "P6", 5: "P1", 4: "P1", 3: "P6", 2: "P1"}
PASS_TARGETS = {
    "4": (0.7, 0.45, 2.55),
    "3": (4.5, 0.35, 2.65),
    "2": (8.3, 0.45, 2.55),
    "5": (0.9, 5.8, 2.15),
    "6": (4.5, 3.0, 2.55),
    "1": (8.1, 3.0, 2.55),
}
ATTACK_STARTS = {
    "4": (1.0, -0.8),
    "3": (4.5, -0.8),
    "2": (8.0, -0.8),
    "6": (4.5, -3.25),
    "1": (8.0, -3.25),
    "5": (1.0, -5.5),
}


def match_action_context(
    state: dict[str, Any],
    action: dict[str, Any],
) -> tuple[int | None, dict[str, str]]:
    """Return the runner and legal rotation slots saved for an action's rally."""

    set_number = int(action.get("set_number") or 0)
    rally_number = int(action.get("rally_number") or 0)
    for rally in state.get("rally_history", []):
        if (
            int(rally.get("set_number") or 0) == set_number
            and int(rally.get("rally_number") or 0) == rally_number
        ):
            return int(rally.get("rotation") or 1), dict(rally.get("rotation_slots_before") or {})
    if set_number == int(state.get("current_set") or 0) and rally_number == int(
        state.get("rally_number") or 0
    ):
        return int(state.get("current_rotation") or 1), dict(state.get("rotation_slots") or {})
    return None, {}


def service_reception_position(
    player_id: str,
    role: str,
    rotation: int,
    rotation_slots: dict[str, str],
) -> str | None:
    """Apply Frauenfeld's P1/P5/P6 reception responsibilities."""

    rotation = int(rotation)
    if role == "libero":
        return LIBERO_RECEPTION_POSITIONS.get(rotation)
    if role != "outside":
        return None
    legal_position = next(
        (position for position, positioned_id in rotation_slots.items() if positioned_id == player_id),
        None,
    )
    if legal_position is None:
        return None
    is_front_outside = legal_position in {"4", "3", "2"}
    if rotation == 1:
        return "P1" if is_front_outside else "P5"
    if is_front_outside:
        return "P5"
    libero_position = LIBERO_RECEPTION_POSITIONS.get(rotation)
    return "P6" if libero_position == "P1" else "P1"


def _action_rotation(state: dict[str, Any], action: dict[str, Any]) -> int | None:
    return match_action_context(state, action)[0]


def _rate(successes: int, attempts: int) -> str:
    return f"{successes / attempts:.0%} ({successes}/{attempts})" if attempts else "- (0/0)"


def _attack_report_metrics(
    player_name: str,
    actions: Iterable[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Return the same seven attack metrics shown in the web analysis."""

    values = summarize_match_actions(actions)["attacks"].get(player_name, {})
    total = int(values.get("total", 0))
    return [
        ("Angriffsaktionen (3. Ball)", str(total)),
        (
            "Angreifbare Bälle verwertet - optimale Pässe",
            _rate(
                int(values.get("optimal_pass_point", 0)),
                int(values.get("optimal_pass_total", 0)),
            ),
        ),
        (
            "Angreifbare Bälle verwertet - andere Pässe",
            _rate(
                int(values.get("other_pass_point", 0)),
                int(values.get("other_pass_total", 0)),
            ),
        ),
        ("Schlag", _rate(int(values.get("spike_point", 0)), int(values.get("spike", 0)))),
        ("Finte", _rate(int(values.get("tip_point", 0)), int(values.get("tip", 0)))),
        ("Safe Ball", _rate(int(values.get("safe_point", 0)), int(values.get("safe", 0)))),
        ("Fehlerquote", _rate(int(values.get("error", 0)), total)),
    ]


def _primary_reception_groups(
    actions: Iterable[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """Group the three first-contact categories used on a player's PDF overview."""

    action_list = list(actions)
    return [
        (
            "Serviceabnahme",
            [action for action in action_list if action.get("ball_type") == "serve_receive"],
        ),
        (
            "Gratisballabnahme",
            [action for action in action_list if action.get("ball_type") == "freeball"],
        ),
        (
            "Angriffsabnahme",
            [action for action in action_list if action.get("ball_type") == "attack_defense"],
        ),
    ]


def _three_column_boxes(
    *,
    y: float,
    height: float,
    page_size: tuple[float, float] = LANDSCAPE_A4,
    margin: float = 34,
    gap: float = 12,
) -> tuple[tuple[float, float, float, float], ...]:
    page_width, _ = page_size
    column_width = (page_width - 2 * margin - 2 * gap) / 3
    return tuple((margin + index * (column_width + gap), y, column_width, height) for index in range(3))


def _safe_text(value: Any) -> str:
    return str(value).replace("–", "-").replace("·", "-").replace("→", "->")


def _page_header(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    title: str,
    *,
    page_size: tuple[float, float] = A4,
) -> None:
    width, height = page_size
    pdf.setFillColor(INK)
    pdf.roundRect(30, height - 92, width - 60, 62, 16, fill=1, stroke=0)
    pdf.setFillColor(colors.white)
    pdf.setFont("Helvetica-Bold", 20)
    pdf.drawString(48, height - 59, _safe_text(player.name))
    pdf.setFillColor(PINK)
    pdf.setFont("Helvetica-Bold", 9)
    pdf.drawRightString(width - 48, height - 55, ROLE_LABELS.get(role, role))
    pdf.setFillColor(colors.HexColor("#ded9e1"))
    pdf.setFont("Helvetica", 8)
    pdf.drawRightString(
        width - 48,
        height - 70,
        _safe_text(f"{session.get('match_date', '')} - {session.get('opponent', '')}"),
    )
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 15)
    pdf.drawString(34, height - 122, _safe_text(title))


def _page_footer(
    pdf: canvas.Canvas,
    page_number: int,
    *,
    page_size: tuple[float, float] = A4,
) -> None:
    width, _ = page_size
    pdf.setStrokeColor(LINE)
    pdf.line(30, 26, width - 30, 26)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7)
    pdf.drawString(30, 15, "VBC Frauenfeld Herren 1 - Matchanalyse")
    pdf.drawRightString(width - 30, 15, f"Seite {page_number}")


def _summary_sections(
    player: Any,
    role: str,
    actions: list[dict[str, Any]],
) -> list[tuple[str, list[str]]]:
    summary = summarize_match_actions(actions)
    sections: list[tuple[str, list[str]]] = []
    reception = summary["receptions"].get(player.name)
    service = summary["services"].get(player.name)
    attack = summary["attacks"].get(player.name)
    setter = summary["setters"].get(player.name)
    block = summary["blocks"].get(player.name)
    if reception:
        positive = reception["perfect"] + reception["good"]
        sections.append(
            (
                "Annahme und Abwehr",
                [
                    f"Perfekt oder gut: {_rate(positive, reception['total'])}",
                    f"Okay {reception['okay']} - Schlecht {reception['bad']} - Fehler {reception['error']}",
                    f"Zu tiefe Annahmen: {reception['too_low']}",
                ],
            )
        )
    if setter:
        sections.append(
            (
                "Zuspiel",
                [
                    f"Rechtzeitig unter dem Ball: {_rate(setter['fast'], setter['total'])}",
                    f"Fehler {setter['error']} - Optimal {setter['optimal']} - Hoch {setter['too_high']} - Tief {setter['too_low']}",
                    f"Innen {setter['too_far_inside']} - Aussen {setter['too_far_outside']}",
                    f"Netznah {setter['too_close_net']} - Netzfern {setter['too_far_net']}",
                    f"Zuspielerfinte: {_rate(setter['setter_tip_point'], setter['setter_tip'])}",
                ],
            )
        )
    if attack and role != "libero":
        sections.append(
            (
                "Angriff",
                [
                    f"Angriffe {attack['total']} - Fehlerquote {_rate(attack['error'], attack['total'])}",
                    f"Optimale Pässe verwertet: {_rate(attack['optimal_pass_point'], attack['optimal_pass_total'])}",
                    f"Andere Pässe verwertet: {_rate(attack['other_pass_point'], attack['other_pass_total'])}",
                    f"Schlag {_rate(attack['spike_point'], attack['spike'])} - Finte {_rate(attack['tip_point'], attack['tip'])}",
                    f"Safe Ball {_rate(attack['safe_point'], attack['safe'])}",
                ],
            )
        )
    if block:
        lines = [
            f"Aktionen {block['total']} - Punkte {block['point']} - Touches {block['touch']}",
            f"Block zu {block['closed']} - Fehler {block['error']} - Mitte zu spät {block['middle_late']}",
        ]
        for origin, values in block.get("opponent_origins", {}).items():
            lines.append(
                f"Gegen {OPPONENT_ATTACK_ORIGIN_LABELS[origin]}: {values['total']} Aktionen, "
                f"{values['point']} Punkte, {values['closed']} geschlossen"
            )
        sections.append(("Block", lines))
    if service:
        sections.append(
            (
                "Service",
                [
                    f"Services {service['total']} - Asse {_rate(service['ace'], service['total'])}",
                    f"Sehr gut {service['very_good']} - Gut {service['good']} - Okay {service['okay']}",
                    f"Fehler {service['error']}",
                ],
            )
        )
    if not sections:
        sections.append(("Match", ["Noch keine bewerteten Aktionen gespeichert."]))
    return sections


def _draw_summary_page(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    actions: list[dict[str, Any]],
    page_number: int,
) -> None:
    width, height = A4
    _page_header(pdf, session, player, role, "Persönliche Matchanalyse")
    y = height - 155
    for title, lines in _summary_sections(player, role, actions):
        box_height = 36 + len(lines) * 18
        if y - box_height < 48:
            break
        pdf.setFillColor(colors.white)
        pdf.setStrokeColor(LINE)
        pdf.roundRect(34, y - box_height, width - 68, box_height, 10, fill=1, stroke=1)
        pdf.setFillColor(PINK)
        pdf.roundRect(34, y - box_height, 8, box_height, 4, fill=1, stroke=0)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 11)
        pdf.drawString(54, y - 22, _safe_text(title))
        pdf.setFont("Helvetica", 9)
        line_y = y - 42
        for line in lines:
            pdf.drawString(55, line_y, _safe_text(line)[:105])
            line_y -= 18
        y -= box_height + 10

    performance = build_player_point_performance(
        actions,
        session.get("state", {}).get("rally_history", []),
        lineup_roles=session.get("state", {}).get("lineup_roles"),
        substitutions=session.get("state", {}).get("substitutions", []),
        player_names_by_id={str(player.id): str(player.name)},
    ).get(player.name, {})
    performance = _performance_metrics_for_role(performance, role)
    if performance and y > 82:
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 10)
        pdf.drawString(38, y - 12, "Form am Matchende")
        x = 145
        for metric, label in PERFORMANCE_METRIC_LABELS.items():
            events = performance.get(metric)
            if not events:
                continue
            pdf.setFillColor(colors.HexColor(events[-1]["color"]))
            pdf.roundRect(x, y - 17, 34, 12, 5, fill=1, stroke=0)
            pdf.setFillColor(MUTED)
            pdf.setFont("Helvetica", 7)
            pdf.drawString(x + 39, y - 14, _safe_text(label))
            x += 44 + min(90, pdf.stringWidth(_safe_text(label), "Helvetica", 7))
            if x > width - 100:
                break
    _page_footer(pdf, page_number)


def _interpolate_color(low: colors.Color, high: colors.Color, fraction: float) -> colors.Color:
    fraction = max(0.0, min(1.0, fraction))
    return colors.Color(
        low.red + (high.red - low.red) * fraction,
        low.green + (high.green - low.green) * fraction,
        low.blue + (high.blue - low.blue) * fraction,
    )


def _draw_performance_bar(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    events: list[dict[str, Any]],
    *,
    mark_actions: bool = False,
) -> None:
    """Draw a clipped, softly blended match timeline for one performance metric."""

    if not events:
        return
    start_color = colors.HexColor(PERFORMANCE_LEVEL_COLORS[3])
    segment_width = width / (len(events) + 1)
    start_fill_color = (
        start_color if events[0].get("on_court") is not False else colors.HexColor(PERFORMANCE_BENCH_COLOR)
    )
    pdf.saveState()
    clip = pdf.beginPath()
    clip.roundRect(x, y, width, height, height / 2)
    pdf.clipPath(clip, stroke=0, fill=0)
    pdf.setFillColor(start_fill_color)
    pdf.rect(x, y, segment_width, height, fill=1, stroke=0)

    previous_color = start_color
    previous_set: int | None = None
    set_boundaries: list[tuple[float, int]] = []
    blend_steps = 12
    for event_index, event in enumerate(events):
        current_color = colors.HexColor(str(event["color"]))
        segment_x = x + (event_index + 1) * segment_width
        if event.get("on_court") is False:
            pdf.setFillColor(colors.HexColor(PERFORMANCE_BENCH_COLOR))
            pdf.rect(segment_x, y, segment_width + 0.2, height, fill=1, stroke=0)
        else:
            for step in range(blend_steps):
                fraction = (step + 0.5) / blend_steps
                pdf.setFillColor(_interpolate_color(previous_color, current_color, fraction))
                step_x = segment_x + step * segment_width / blend_steps
                pdf.rect(step_x, y, segment_width / blend_steps + 0.2, height, fill=1, stroke=0)
        set_number = int(event.get("set_number") or 1)
        if previous_set not in {None, set_number}:
            set_boundaries.append((segment_x, set_number))
        previous_set = set_number
        previous_color = current_color
    pdf.restoreState()

    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(0.8)
    pdf.roundRect(x, y, width, height, height / 2, fill=0, stroke=1)
    for boundary_x, _ in set_boundaries:
        pdf.setStrokeColor(INK)
        pdf.setLineWidth(1.4)
        pdf.line(boundary_x, y, boundary_x, y + height)

    if mark_actions:
        pdf.setFillColor(INK)
        marker_y = y + height + 2
        for event_index, event in enumerate(events):
            if not event.get("had_action"):
                continue
            marker_x = x + (event_index + 1) * segment_width
            marker_width = max(1.2, segment_width * 0.72)
            pdf.roundRect(
                marker_x + (segment_width - marker_width) / 2,
                marker_y,
                marker_width,
                3,
                1.5,
                fill=1,
                stroke=0,
            )


def _draw_performance_page(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    actions: list[dict[str, Any]],
    page_number: int,
) -> int:
    performance = build_player_point_performance(
        actions,
        session.get("state", {}).get("rally_history", []),
        lineup_roles=session.get("state", {}).get("lineup_roles"),
        substitutions=session.get("state", {}).get("substitutions", []),
        player_names_by_id={str(player.id): str(player.name)},
    ).get(player.name, {})
    performance = _performance_metrics_for_role(performance, role)
    if not performance:
        return page_number

    pdf.showPage()
    pdf.setPageSize(A4)
    page_number += 1
    _page_header(pdf, session, player, role, "Formverlauf")
    width, height = A4
    content_x = 42
    content_width = width - 84

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 8)
    pdf.drawString(content_x, height - 151, "Formtief")
    legend_x = content_x + 51
    legend_width = 210
    legend_y = height - 158
    legend_steps = 60
    low_color = colors.HexColor(PERFORMANCE_LEVEL_COLORS[0])
    high_color = colors.HexColor(PERFORMANCE_LEVEL_COLORS[-1])
    for step in range(legend_steps):
        pdf.setFillColor(_interpolate_color(low_color, high_color, step / (legend_steps - 1)))
        pdf.rect(
            legend_x + step * legend_width / legend_steps,
            legend_y,
            legend_width / legend_steps + 0.2,
            9,
            fill=1,
            stroke=0,
        )
    pdf.setFillColor(MUTED)
    pdf.drawString(legend_x + legend_width + 7, height - 151, "Formhoch")
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 8)
    pdf.drawRightString(width - content_x, height - 151, "Start: mittlere gelbe Stufe")

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 7.5)
    performance_note = (
        "Annahme: jede 2. zu tiefe Serviceannahme -1; sonst jede 3.; perfekter Gratisball +2 unter Gelb."
    )
    pdf.drawString(
        content_x,
        height - 177,
        performance_note,
    )
    pdf.drawString(
        content_x,
        height - 190,
        (
            "Grau = nicht auf dem Feld; schwarze Linien = Satzwechsel; Form läuft im Match weiter."
            if role == "libero"
            else "Angriff: 6/10 Chancen; nur vorne und auf Feld, Dia überall auf Feld."
        ),
    )
    if role != "libero":
        pdf.drawString(
            content_x,
            height - 203,
            "Grau = nicht auf dem Feld; schwarze Linien = Satzwechsel; Form läuft im Match weiter.",
        )
    note_y = 216
    if role == "middle":
        pdf.drawString(
            content_x,
            height - note_y,
            "Block: 5-mal zu langsam -1; 7-mal Block zu oder 5 Touches +1 (matchweit).",
        )
        note_y += 13
    if role == "setter":
        pdf.drawString(
            content_x,
            height - note_y,
            "Zuspieler: 2-mal rechtzeitig +1; Passfehler sofort -1; sonst optimal +1 oder 3/5 nicht optimal -1.",
        )
        note_y += 13

    if "service" in performance:
        pdf.drawString(
            content_x,
            height - note_y,
            "Service: kleine schwarze Balken markieren jeden eigenen Service.",
        )

    row_y = height - (
        245 + (13 if role in {"middle", "setter"} else 0) + (15 if "service" in performance else 0)
    )
    for metric in PERFORMANCE_METRIC_LABELS:
        events = performance.get(metric)
        if not events:
            continue
        action_count = sum(int(bool(event.get("had_action"))) for event in events)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 9)
        pdf.drawString(content_x, row_y + 30, _safe_text(PERFORMANCE_METRIC_LABELS[metric]))
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 7)
        pdf.drawRightString(
            width - content_x,
            row_y + 30,
            _safe_text(f"{action_count} Punkte mit Aktion"),
        )
        _draw_performance_bar(
            pdf,
            content_x,
            row_y,
            content_width,
            20,
            events,
            mark_actions=metric == "service",
        )
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 6.5)
        pdf.drawString(content_x, row_y - 12, "Matchbeginn")
        pdf.drawRightString(width - content_x, row_y - 12, "Matchende")
        row_y -= 88

    _page_footer(pdf, page_number)
    return page_number


def _quality_line(actions: list[dict[str, Any]]) -> str:
    counts = Counter(action.get("first_contact_quality") for action in actions)
    return (
        " - ".join(
            f"{FIRST_CONTACT_LABELS[key].split(' - ')[0].split(' · ')[0]} {counts[key]}"
            for key in ("perfect", "good", "okay", "bad", "error")
            if counts[key]
        )
        or "Keine bewerteten Annahmen"
    )


def _draw_heatmap(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    actions: list[dict[str, Any]],
) -> None:
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, y + height - 14, _safe_text(f"{title} ({len(actions)})")[:75])
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawString(x, y + height - 27, _safe_text(_quality_line(actions))[:105])
    counts = Counter(
        (int(action["first_contact_x"]), int(action["first_contact_y"]))
        for action in actions
        if action.get("first_contact_x") is not None
        and action.get("first_contact_y") is not None
        and 0 <= int(action["first_contact_x"]) <= 8
        and 0 <= int(action["first_contact_y"]) <= 8
    )
    max_count = max(counts.values(), default=1)
    side = min(width - 8, height - 58)
    cell = side / 9
    field_x = x + (width - side) / 2
    field_top = y + height - 42
    for cell_y in range(9):
        for cell_x in range(9):
            count = counts[(cell_x, cell_y)]
            fill = PALE if not count else _interpolate_color(PINK, PURPLE, count / max_count)
            pdf.setFillColor(fill)
            pdf.setStrokeColor(colors.white)
            pdf.setLineWidth(0.35)
            pdf.rect(
                field_x + cell_x * cell,
                field_top - (cell_y + 1) * cell,
                cell,
                cell,
                fill=1,
                stroke=1,
            )
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(3)
    pdf.line(field_x, field_top, field_x + side, field_top)
    pdf.setStrokeColor(colors.white)
    pdf.setLineWidth(1.6)
    pdf.line(field_x, field_top - 3 * cell, field_x + side, field_top - 3 * cell)
    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(0.6)
    pdf.rect(field_x, field_top - side, side, side, fill=0, stroke=1)
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6)
    pdf.drawString(field_x, field_top - side - 10, "Netz oben - keine Zahlen in den Feldern")


def _group_origin_2x2(x: float, y: float) -> tuple[float, float]:
    return min(7.0, max(1.0, int(x // 2) * 2 + 1.0)), min(7.0, max(1.0, int(y // 2) * 2 + 1.0))


def _pass_color(tendencies: tuple[str, ...]) -> colors.Color:
    if "error" in tendencies:
        return RED
    if "too_low" in tendencies:
        return BLUE
    if "too_high" in tendencies:
        return ORANGE
    if "too_far_inside" in tendencies:
        return TURQUOISE
    if "too_far_outside" in tendencies:
        return YELLOW
    return GREEN


def _pass_points(action: dict[str, Any]) -> list[tuple[float, float]]:
    tendencies = parse_set_tendencies(action.get("set_tendency"))
    start_x, start_y = _group_origin_2x2(float(action["set_origin_x"]), float(action["set_origin_y"]))
    target_x, target_y, target_height = PASS_TARGETS[action["attack_origin"]]
    inside_meters = float(action.get("set_inside_meters") or 0)
    if "too_far_inside" in tendencies:
        shift = max(0.5, inside_meters)
        target_x += shift if target_x <= 4.5 else -shift
    if "too_far_outside" in tendencies:
        target_x = -1.4 if target_x < 4.5 else 10.4
    if action["attack_origin"] == "3":
        if "too_low" in tendencies:
            target_height, lift = 2.31, 0.12
        elif "too_high" in tendencies:
            target_height, lift = 3.45, 0.65
        else:
            target_height, lift = 2.68, 0.38
    elif "too_high" in tendencies:
        lift = 3.15
    elif "too_low" in tendencies:
        lift = 1.15
    else:
        lift = 2.1

    def project(court_x: float, court_y: float, z: float = 0.0) -> tuple[float, float]:
        return 62 + court_x * 31 + court_y * 13, 285 + court_y * 9.8 - z * 43

    points = []
    for step in range(29):
        progress = step / 28
        court_x = start_x + (target_x - start_x) * progress
        court_y = start_y + (target_y - start_y) * progress
        base_height = 2.15 + (target_height - 2.15) * progress
        ball_height = base_height + 4 * progress * (1 - progress) * lift
        points.append(project(court_x, court_y, ball_height))
    return points


def _draw_arrow_head(
    pdf: canvas.Canvas,
    start: tuple[float, float],
    end: tuple[float, float],
    color: colors.Color,
    size: float = 4,
) -> None:
    angle = atan2(end[1] - start[1], end[0] - start[0])
    pdf.setFillColor(color)
    path = pdf.beginPath()
    path.moveTo(end[0], end[1])
    path.lineTo(end[0] - size * cos(angle - pi / 6), end[1] - size * sin(angle - pi / 6))
    path.lineTo(end[0] - size * cos(angle + pi / 6), end[1] - size * sin(angle + pi / 6))
    path.close()
    pdf.drawPath(path, fill=1, stroke=0)


def _draw_volleyball_net(
    pdf: canvas.Canvas,
    project: Callable[[float, float, float], tuple[float, float]],
) -> None:
    """Draw the same one-metre women's net used in the web trajectory view."""

    floor_left = project(0, 0, 0)
    floor_right = project(9, 0, 0)
    bottom_left = project(0, 0, WOMENS_NET_BOTTOM_METERS)
    bottom_right = project(9, 0, WOMENS_NET_BOTTOM_METERS)
    top_left = project(0, 0, WOMENS_NET_TOP_METERS)
    top_right = project(9, 0, WOMENS_NET_TOP_METERS)
    pole_top_left = project(0, 0, 2.65)
    pole_top_right = project(9, 0, 2.65)

    pdf.saveState()
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(2.3)
    pdf.line(*floor_left, *pole_top_left)
    pdf.line(*floor_right, *pole_top_right)

    body = pdf.beginPath()
    body.moveTo(*bottom_left)
    body.lineTo(*bottom_right)
    body.lineTo(*top_right)
    body.lineTo(*top_left)
    body.close()
    pdf.setFillColor(colors.HexColor("#faf9fb"))
    pdf.drawPath(body, fill=1, stroke=0)

    pdf.setStrokeColor(colors.HexColor("#514b57"))
    pdf.setLineWidth(0.35)
    for height in net_mesh_heights():
        pdf.line(*project(0, 0, height), *project(9, 0, height))
    for court_x in net_mesh_positions():
        pdf.line(
            *project(court_x, 0, WOMENS_NET_BOTTOM_METERS),
            *project(court_x, 0, WOMENS_NET_TOP_METERS),
        )

    pdf.setLineWidth(0.9)
    pdf.line(*bottom_left, *bottom_right)
    for start, end in ((bottom_left, top_left), (bottom_right, top_right)):
        pdf.setStrokeColor(INK)
        pdf.setLineWidth(2.6)
        pdf.line(*start, *end)
        pdf.setStrokeColor(colors.white)
        pdf.setLineWidth(1.25)
        pdf.line(*start, *end)
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(3.2)
    pdf.line(*top_left, *top_right)
    pdf.setStrokeColor(colors.white)
    pdf.setLineWidth(1.35)
    pdf.line(*top_left, *top_right)
    pdf.restoreState()


def _draw_pass_origin_markers(
    pdf: canvas.Canvas,
    actions: Iterable[dict[str, Any]],
    project: Callable[[float, float, float], tuple[float, float]],
) -> None:
    """Mark each grouped setting origin and project it onto the court floor."""

    origins = sorted(
        {
            _group_origin_2x2(
                float(action["set_origin_x"]),
                float(action["set_origin_y"]),
            )
            for action in actions
        }
    )
    if not origins:
        return

    pdf.saveState()
    pdf.setStrokeColor(PURPLE)
    pdf.setLineWidth(0.65)
    pdf.setDash(2, 2)
    projected_origins: list[tuple[float, float]] = []
    for court_x, court_y in origins:
        floor = project(court_x, court_y, 0)
        marker = project(court_x, court_y, 2.15)
        pdf.line(*floor, *marker)
        projected_origins.append(marker)

    pdf.setDash()
    pdf.setFillColor(PINK)
    pdf.setStrokeColor(PURPLE)
    pdf.setLineWidth(1.25)
    for marker_x, marker_y in projected_origins:
        pdf.circle(marker_x, marker_y, 4.6, fill=1, stroke=1)
    pdf.restoreState()


def _draw_pass_chart(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    actions: list[dict[str, Any]],
) -> None:
    valid = [
        action
        for action in actions
        if action.get("set_origin_x") is not None
        and action.get("set_origin_y") is not None
        and action.get("attack_origin") in PASS_TARGETS
        and parse_set_tendencies(action.get("set_tendency"))
        and action.get("attack_type") not in {"setter_tip", "second_ball_return"}
    ]
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawString(x, y + height - 14, _safe_text(f"{title} ({len(valid)})")[:78])
    trait_counts = Counter(
        tendency for action in valid for tendency in parse_set_tendencies(action.get("set_tendency"))
    )
    trait_line = " - ".join(
        f"{SET_TENDENCY_LABELS[key]} {trait_counts[key]}"
        for key in ("error", "optimal", "too_high", "too_low", "too_far_inside", "too_far_outside")
        if trait_counts[key]
    )
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawString(x, y + height - 27, _safe_text(trait_line)[:105])
    chart_y = y + 4
    chart_h = height - 38

    def transform(point: tuple[float, float]) -> tuple[float, float]:
        return x + point[0] / 520 * width, chart_y + chart_h - point[1] / 430 * chart_h

    def project(court_x: float, court_y: float, z: float = 0.0) -> tuple[float, float]:
        return transform((62 + court_x * 31 + court_y * 13, 285 + court_y * 9.8 - z * 43))

    corners = [project(0, 0), project(9, 0), project(9, 9), project(0, 9)]
    path = pdf.beginPath()
    path.moveTo(*corners[0])
    for corner in corners[1:]:
        path.lineTo(*corner)
    path.close()
    pdf.setFillColor(PALE)
    pdf.setStrokeColor(LINE)
    pdf.drawPath(path, fill=1, stroke=1)
    for meter_y in (3,):
        left = project(0, meter_y)
        right = project(9, meter_y)
        pdf.setStrokeColor(colors.white)
        pdf.setLineWidth(1.5)
        pdf.line(*left, *right)
    _draw_volleyball_net(pdf, project)

    for action in valid:
        color = _pass_color(parse_set_tendencies(action.get("set_tendency")))
        points = [transform(point) for point in _pass_points(action)]
        pdf.setStrokeColor(color)
        pdf.setLineWidth(1.25)
        for start, end in zip(points, points[1:]):
            pdf.line(*start, *end)
        _draw_arrow_head(pdf, points[-2], points[-1], color, 3.5)
    _draw_pass_origin_markers(pdf, valid, project)
    pdf.setFont("Helvetica", 6)
    pdf.setFillColor(MUTED)
    pdf.drawString(
        x + 4,
        chart_y + 3,
        "Gruen optimal - Orange hoch - Blau tief - Tuerkis innen - Gelb aussen - Rot Fehler",
    )


def _draw_attack_chart(
    pdf: canvas.Canvas,
    x: float,
    y: float,
    width: float,
    height: float,
    title: str,
    actions: list[dict[str, Any]],
) -> None:
    valid = [
        action
        for action in actions
        if action.get("attack_origin") in ATTACK_STARTS
        and (
            (action.get("landing_x") is not None and action.get("landing_y") is not None)
            or action.get("attack_block_outcome") in {"blockout", "recycle_us", "blocked_point"}
        )
    ]
    pdf.setFillColor(INK)
    pdf.setFont("Helvetica-Bold", 10)
    pdf.drawCentredString(x + width / 2, y + height - 14, _safe_text(f"{title} ({len(valid)})"))
    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica", 6.5)
    pdf.drawCentredString(x + width / 2, y + height - 27, "Gegnerisches Feld")

    map_bottom = y + 14
    map_top = y + height - 40
    side = min(width - 34, (map_top - map_bottom) / (2 + 1 / 9))
    field_x = x + (width - side) / 2
    own_bottom = map_bottom
    net_y = own_bottom + side
    out_margin = side / 9

    pdf.setFillColor(colors.HexColor("#e5e1e6"))
    pdf.setStrokeColor(colors.HexColor("#e5e1e6"))
    pdf.roundRect(
        field_x - out_margin,
        net_y - out_margin,
        side + 2 * out_margin,
        side + 2 * out_margin,
        3,
        fill=1,
        stroke=0,
    )
    pdf.setFillColor(PINK)
    pdf.setStrokeColor(colors.white)
    pdf.setLineWidth(1.4)
    pdf.rect(field_x, net_y, side, side, fill=1, stroke=1)
    pdf.setFillColor(PALE)
    pdf.setStrokeColor(LINE)
    pdf.setLineWidth(1.2)
    pdf.rect(field_x, own_bottom, side, side, fill=1, stroke=1)

    pdf.setStrokeColor(colors.white)
    pdf.setLineWidth(1.4)
    pdf.line(field_x, net_y + side / 3, field_x + side, net_y + side / 3)
    pdf.line(field_x, net_y - side / 3, field_x + side, net_y - side / 3)
    pdf.setStrokeColor(INK)
    pdf.setLineWidth(3.2)
    pdf.line(field_x, net_y, field_x + side, net_y)

    pdf.setFillColor(MUTED)
    pdf.setFont("Helvetica-Bold", 6)
    pdf.drawCentredString(field_x + side / 2, net_y - 9, "NETZ")
    pdf.setFillColor(colors.HexColor("#8b8490"))
    pdf.setFont("Helvetica", 5.5)
    pdf.drawCentredString(field_x + side / 2, net_y - side / 3 - 8, "3-M-LINIE")

    def map_x(value: float) -> float:
        return field_x + value / 9 * side

    def map_y(value: float) -> float:
        return net_y + value / 9 * side

    for action in valid:
        start_x, start_y = ATTACK_STARTS[action["attack_origin"]]
        if action.get("landing_x") is not None and action.get("landing_y") is not None:
            target_x = int(action["landing_x"]) + 0.5
            target_y = int(action["landing_y"]) + 0.5
        else:
            target_x = start_x
            target_y = 0.15
        jitter = ((int(action.get("id") or 0) % 5) - 2) * 0.045
        sx = map_x(start_x + jitter)
        sy = map_y(start_y)
        tx = map_x(target_x + jitter)
        ty = map_y(target_y)
        result = action.get("attack_result")
        color = GREEN if result == "point" else RED if result == "error" else INK
        pdf.setStrokeColor(color)
        pdf.setLineWidth(1.2)
        pdf.line(sx, sy, tx, ty)
        _draw_arrow_head(pdf, (sx, sy), (tx, ty), color, 3.1)

    for origin in sorted({action["attack_origin"] for action in valid}):
        origin_x, origin_y = ATTACK_STARTS[origin]
        marker_x = map_x(origin_x)
        marker_y = map_y(origin_y)
        pdf.setFillColor(colors.white)
        pdf.setStrokeColor(INK)
        pdf.setLineWidth(0.9)
        pdf.circle(marker_x, marker_y, 5.2, fill=1, stroke=1)
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 5.5)
        pdf.drawCentredString(marker_x, marker_y - 1.8, f"P{origin}")

    if not valid:
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 7)
        pdf.drawCentredString(field_x + side / 2, net_y + side / 2, "Noch keine Bälle gespeichert")


def _draw_attack_metric_cards(
    pdf: canvas.Canvas,
    metrics: list[tuple[str, str]],
    *,
    y: float,
    height: float,
    page_size: tuple[float, float] = LANDSCAPE_A4,
) -> None:
    page_width, _ = page_size
    margin = 34
    gap = 7
    card_width = (page_width - 2 * margin - gap * (len(metrics) - 1)) / len(metrics)
    label_lines = {
        "Angriffsaktionen (3. Ball)": ("Angriffsaktionen", "(3. Ball)"),
        "Angreifbare Bälle verwertet - optimale Pässe": (
            "Angreifbare Bälle",
            "verwertet",
            "optimale Pässe",
        ),
        "Angreifbare Bälle verwertet - andere Pässe": (
            "Angreifbare Bälle",
            "verwertet",
            "andere Pässe",
        ),
    }
    for index, (label, value) in enumerate(metrics):
        card_x = margin + index * (card_width + gap)
        pdf.setFillColor(colors.white)
        pdf.setStrokeColor(LINE)
        pdf.setLineWidth(0.7)
        pdf.roundRect(card_x, y, card_width, height, 7, fill=1, stroke=1)
        pdf.setFillColor(PINK)
        pdf.roundRect(card_x, y + height - 5, card_width, 5, 2.5, fill=1, stroke=0)
        pdf.setFillColor(MUTED)
        pdf.setFont("Helvetica", 6.2)
        lines = label_lines.get(label, (label,))
        line_y = y + height - 18
        for line in lines:
            pdf.drawCentredString(card_x + card_width / 2, line_y, _safe_text(line))
            line_y -= 8
        pdf.setFillColor(INK)
        pdf.setFont("Helvetica-Bold", 9.5)
        pdf.drawCentredString(card_x + card_width / 2, y + 11, _safe_text(value))


def _draw_attack_overview_page(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    actions: list[dict[str, Any]],
    attacks: list[dict[str, Any]],
    page_number: int,
) -> int:
    pdf.showPage()
    pdf.setPageSize(LANDSCAPE_A4)
    page_number += 1
    _page_header(
        pdf,
        session,
        player,
        role,
        "Angriff",
        page_size=LANDSCAPE_A4,
    )
    _draw_attack_metric_cards(
        pdf,
        _attack_report_metrics(player.name, actions),
        y=385,
        height=70,
    )
    for (box_x, box_y, box_width, box_height), attack_type in zip(
        _three_column_boxes(y=47, height=320),
        THIRD_BALL_ATTACK_TYPES,
    ):
        _draw_attack_chart(
            pdf,
            box_x,
            box_y,
            box_width,
            box_height,
            ATTACK_TYPE_LABELS[attack_type],
            [action for action in attacks if action.get("attack_type") == attack_type],
        )
    _page_footer(pdf, page_number, page_size=LANDSCAPE_A4)
    return page_number


def _draw_reception_overview_page(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    reception_actions: list[dict[str, Any]],
    page_number: int,
) -> int:
    pdf.showPage()
    pdf.setPageSize(LANDSCAPE_A4)
    page_number += 1
    _page_header(
        pdf,
        session,
        player,
        role,
        "Annahme-Heatmaps",
        page_size=LANDSCAPE_A4,
    )
    for (box_x, box_y, box_width, box_height), (title, grouped) in zip(
        _three_column_boxes(y=46, height=409),
        _primary_reception_groups(reception_actions),
    ):
        _draw_heatmap(
            pdf,
            box_x,
            box_y,
            box_width,
            box_height,
            title,
            grouped,
        )
    _page_footer(pdf, page_number, page_size=LANDSCAPE_A4)
    return page_number


def _draw_chart_pages(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    page_title: str,
    entries: list[tuple[str, list[dict[str, Any]]]],
    drawer: Callable[..., None],
    page_number: int,
) -> int:
    width, height = A4
    for entry_index in range(0, len(entries), 2):
        pdf.showPage()
        pdf.setPageSize(A4)
        page_number += 1
        _page_header(pdf, session, player, role, page_title)
        pair = entries[entry_index : entry_index + 2]
        for index, (title, entry_actions) in enumerate(pair):
            chart_height = 300
            chart_y = height - 455 - index * 325
            drawer(pdf, 38, chart_y, width - 76, chart_height, title, entry_actions)
        _page_footer(pdf, page_number)
    return page_number


def _draw_landscape_chart_pages(
    pdf: canvas.Canvas,
    session: dict[str, Any],
    player: Any,
    role: str,
    page_title: str,
    entries: list[tuple[str, list[dict[str, Any]]]],
    drawer: Callable[..., None],
    page_number: int,
) -> int:
    """Draw detail charts as three readable columns on landscape pages."""

    for entry_index in range(0, len(entries), 3):
        pdf.showPage()
        pdf.setPageSize(LANDSCAPE_A4)
        page_number += 1
        _page_header(
            pdf,
            session,
            player,
            role,
            page_title,
            page_size=LANDSCAPE_A4,
        )
        for box, (title, entry_actions) in zip(
            _three_column_boxes(y=46, height=409),
            entries[entry_index : entry_index + 3],
        ):
            drawer(pdf, *box, title, entry_actions)
        _page_footer(pdf, page_number, page_size=LANDSCAPE_A4)
    return page_number


def _performance_metrics_for_role(
    performance: dict[str, list[dict[str, Any]]],
    role: str,
) -> dict[str, list[dict[str, Any]]]:
    """Remove form categories that are not part of a player's role report."""

    return {
        metric: events
        for metric, events in performance.items()
        if not (role == "libero" and metric == "attack") and not (role != "middle" and metric == "block")
    }


def player_analysis_pdf(
    player: Any,
    role: str,
    session: dict[str, Any],
    actions: Iterable[dict[str, Any]],
) -> bytes:
    """Build a multi-page, role-specific match analysis for one player."""

    action_list = list(actions)
    player_actions = [
        action
        for action in action_list
        if player.id
        in {
            action.get("server_id"),
            action.get("receiver_id"),
            action.get("setter_id"),
            action.get("attacker_id"),
            action.get("block_player_id"),
        }
    ]
    output = BytesIO()
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    page_number = 1
    _draw_summary_page(pdf, session, player, role, action_list, page_number)
    page_number = _draw_performance_page(
        pdf,
        session,
        player,
        role,
        action_list,
        page_number,
    )
    state = session.get("state", {})

    setter_actions = [
        action
        for action in action_list
        if action.get("setter_id") == player.id
        and action.get("set_origin_x") is not None
        and action.get("set_origin_y") is not None
        and action.get("attack_origin") in PASS_TARGETS
        and parse_set_tendencies(action.get("set_tendency"))
        and action.get("attack_type") not in {"setter_tip", "second_ball_return"}
    ]
    if role == "setter" and setter_actions:
        by_player = [
            (name, [action for action in setter_actions if action.get("attacker_name") == name])
            for name in sorted(
                {action.get("attacker_name") for action in setter_actions if action.get("attacker_name")}
            )
        ]
        by_rotation = [
            (
                f"Läufer L{rotation}",
                [action for action in setter_actions if _action_rotation(state, action) == rotation],
            )
            for rotation in range(1, 7)
            if any(_action_rotation(state, action) == rotation for action in setter_actions)
        ]
        by_position = [
            (
                f"Angriffsposition P{position}",
                [action for action in setter_actions if action.get("attack_origin") == position],
            )
            for position in ("4", "3", "2", "6", "1", "5")
            if any(action.get("attack_origin") == position for action in setter_actions)
        ]
        page_number = _draw_chart_pages(
            pdf,
            session,
            player,
            role,
            "Passflugbahnen pro Angreifer",
            by_player,
            _draw_pass_chart,
            page_number,
        )
        page_number = _draw_chart_pages(
            pdf,
            session,
            player,
            role,
            "Passflugbahnen pro Läufer",
            by_rotation,
            _draw_pass_chart,
            page_number,
        )
        page_number = _draw_chart_pages(
            pdf,
            session,
            player,
            role,
            "Passflugbahnen pro Angriffsposition",
            by_position,
            _draw_pass_chart,
            page_number,
        )

    reception_actions = [
        action
        for action in action_list
        if action.get("receiver_id") == player.id
        and action.get("first_contact_x") is not None
        and action.get("first_contact_y") is not None
    ]
    if reception_actions:
        primary_reception_groups = _primary_reception_groups(reception_actions)
        page_number = _draw_reception_overview_page(
            pdf,
            session,
            player,
            role,
            reception_actions,
            page_number,
        )
        service_actions = primary_reception_groups[0][1]
        defense_actions = primary_reception_groups[2][1]
        reception_details: list[tuple[str, list[dict[str, Any]]]] = []
        if service_actions:
            position_groups: dict[str, list[dict[str, Any]]] = {"P1": [], "P5": [], "P6": []}
            for action in service_actions:
                rotation, rotation_slots = match_action_context(state, action)
                if rotation is None:
                    continue
                position = service_reception_position(player.id, role, rotation, rotation_slots)
                if position in position_groups:
                    position_groups[position].append(action)
            reception_details.extend(
                (f"Serviceabnahme von {position}", grouped)
                for position, grouped in position_groups.items()
                if grouped
            )
        if defense_actions:
            origins_by_rally = {
                (int(action.get("set_number") or 0), int(action.get("rally_number") or 0)): action.get(
                    "opponent_attack_origin"
                )
                for action in action_list
                if action.get("ball_type") == "block" and action.get("opponent_attack_origin")
            }
            for origin in OPPONENT_ATTACK_ORIGIN_LABELS:
                grouped = [
                    action
                    for action in defense_actions
                    if origins_by_rally.get(
                        (int(action.get("set_number") or 0), int(action.get("rally_number") or 0))
                    )
                    == origin
                ]
                if grouped:
                    reception_details.append(
                        (f"Angriffsabnahme gegen {OPPONENT_ATTACK_ORIGIN_LABELS[origin]}", grouped)
                    )
        if reception_details:
            page_number = _draw_landscape_chart_pages(
                pdf,
                session,
                player,
                role,
                "Annahme im Detail",
                reception_details,
                _draw_heatmap,
                page_number,
            )

    attacks = [
        action
        for action in player_actions
        if action.get("attacker_id") == player.id and action.get("attack_type") in THIRD_BALL_ATTACK_TYPES
    ]
    if role != "libero" and attacks:
        page_number = _draw_attack_overview_page(
            pdf,
            session,
            player,
            role,
            action_list,
            attacks,
            page_number,
        )

    pdf.save()
    return output.getvalue()
