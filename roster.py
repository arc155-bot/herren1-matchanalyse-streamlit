from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable
from uuid import uuid4


POSITION_LABELS = {
    "setter": "Pass (Zuspiel)",
    "opposite": "Diagonal",
    "outside": "Aussen",
    "middle": "Mitte",
    "libero": "Libero",
}
POSITION_OPTIONS = tuple(POSITION_LABELS)


@dataclass(frozen=True)
class Player:
    id: str
    name: str
    primary_position: str
    secondary_positions: tuple[str, ...] = ()
    backup_setter: bool = False


DEFAULT_ROSTER: tuple[Player, ...] = (
    Player("h1_s1", "Giovanni", "setter"),
    Player("h1_s2", "Matthäus", "setter"),
    Player("h1_d1", "Raschad", "opposite"),
    Player("h1_d2", "Labisan", "opposite"),
    Player("h1_a1", "Rohan", "outside"),
    Player("h1_a2", "Mike", "outside"),
    Player("h1_a3", "Zaki", "outside"),
    Player(
        "h1_f1",
        "Cameron",
        "outside",
        secondary_positions=("opposite", "setter"),
        backup_setter=True,
    ),
    Player("h1_m1", "Maurice", "middle"),
    Player("h1_m2", "Jan", "middle"),
    Player("h1_m3", "Luan", "middle"),
    Player("h1_m4", "Solomon", "middle"),
    Player("h1_l1", "Kevin", "libero"),
)


def player_label(player: Player) -> str:
    roles = [POSITION_LABELS[player.primary_position]]
    roles.extend(POSITION_LABELS[position] for position in player.secondary_positions)
    player_positions = (player.primary_position, *player.secondary_positions)
    if player.backup_setter and "setter" not in player_positions:
        roles.append("Backup-Pass")
    return f"{player.name} · {' / '.join(roles)}"


def roster_path(base_dir: Path) -> Path:
    return Path(base_dir) / "daten" / "kader.json"


def load_roster(base_dir: Path) -> tuple[Player, ...]:
    path = roster_path(base_dir)
    if not path.exists():
        return DEFAULT_ROSTER
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, list):
            raise ValueError("Der Kader muss eine Liste sein.")
        players = players_from_rows(payload)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return DEFAULT_ROSTER
    if not players:
        return DEFAULT_ROSTER
    updated_players = _apply_h1_roster_updates(players)
    if updated_players != players:
        save_roster(base_dir, updated_players)
    return updated_players


def save_roster(base_dir: Path, players: Iterable[Player]) -> Path:
    clean = _deduplicate(players)
    if not clean:
        raise ValueError("Mindestens ein Spieler ist erforderlich.")
    path = roster_path(base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps([asdict(player) for player in clean], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def roster_rows(players: Iterable[Player]) -> list[dict[str, Any]]:
    return [
        {
            "id": player.id,
            "name": player.name,
            "primary_position": player.primary_position,
            "secondary_positions": ", ".join(player.secondary_positions),
            "backup_setter": player.backup_setter,
        }
        for player in players
    ]


def players_from_rows(rows: Iterable[dict[str, Any]]) -> tuple[Player, ...]:
    players: list[Player] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        primary = str(row.get("primary_position") or "outside").strip()
        if primary not in POSITION_OPTIONS:
            primary = "outside"
        raw_secondary = row.get("secondary_positions") or ()
        if isinstance(raw_secondary, str):
            raw_secondary = raw_secondary.split(",")
        secondary = tuple(
            position
            for position in _unique(str(value).strip() for value in raw_secondary)
            if position in POSITION_OPTIONS and position != primary
        )
        player_id = str(row.get("id") or "").strip() or f"h1_{uuid4().hex[:10]}"
        players.append(
            Player(
                id=player_id,
                name=name,
                primary_position=primary,
                secondary_positions=secondary,
                backup_setter=bool(row.get("backup_setter")),
            )
        )
    return _deduplicate(players)


def has_complete_lineup(players: Iterable[Player]) -> bool:
    values = tuple(players)
    counts = {
        position: sum(
            position == player.primary_position or position in player.secondary_positions
            for player in values
        )
        for position in POSITION_OPTIONS
    }
    return (
        counts["setter"] >= 1
        and counts["opposite"] >= 1
        and counts["outside"] >= 2
        and counts["middle"] >= 2
        and counts["libero"] >= 1
    )


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return tuple(result)


def _apply_h1_roster_updates(players: tuple[Player, ...]) -> tuple[Player, ...]:
    """Apply confirmed Herren-1 corrections to an already saved roster."""

    updated: list[Player] = []
    for player in players:
        if player.id == "h1_d2" and player.name != "Labisan":
            player = Player(
                id=player.id,
                name="Labisan",
                primary_position=player.primary_position,
                secondary_positions=player.secondary_positions,
                backup_setter=player.backup_setter,
            )
        updated.append(player)

    has_zaki = any(player.id == "h1_a3" or player.name.casefold() == "zaki" for player in updated)
    if not has_zaki:
        insert_at = next(
            (index + 1 for index, player in enumerate(updated) if player.id == "h1_a2"),
            len(updated),
        )
        updated.insert(insert_at, Player("h1_a3", "Zaki", "outside"))
    return _deduplicate(updated)


def _deduplicate(players: Iterable[Player]) -> tuple[Player, ...]:
    result: list[Player] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    for player in players:
        name_key = player.name.casefold()
        if player.id in seen_ids or name_key in seen_names:
            continue
        seen_ids.add(player.id)
        seen_names.add(name_key)
        result.append(player)
    return tuple(result)
