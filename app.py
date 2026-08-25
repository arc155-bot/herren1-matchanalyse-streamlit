from __future__ import annotations

from pathlib import Path

import streamlit as st

from match_live import render_match_analysis
from roster import (
    DEFAULT_ROSTER,
    POSITION_LABELS,
    POSITION_OPTIONS,
    has_complete_lineup,
    load_roster,
    player_label,
    players_from_rows,
    roster_rows,
    save_roster,
)
from storage_schema import SCHEMA_VERSION, init_db


BASE_DIR = Path(__file__).resolve().parent


def configure_page() -> None:
    st.set_page_config(
        page_title="Herren 1 · Matchanalyse",
        page_icon="🏐",
        layout="wide",
        initial_sidebar_state="auto",
    )
    st.markdown(
        """
        <style>
        :root { color-scheme: dark; }
        .stApp {
            background:
                radial-gradient(circle at 78% 3%, rgba(139, 44, 255, .16), transparent 30rem),
                linear-gradient(155deg, #080510 0%, #11071d 48%, #07050c 100%);
        }
        [data-testid="stSidebar"] {
            background: linear-gradient(180deg, #10071b 0%, #09050f 100%);
            border-right: 1px solid rgba(190, 125, 255, .22);
        }
        h1, h2, h3 { letter-spacing: -.02em; }
        div[data-testid="stMetric"] {
            border: 1px solid rgba(190, 125, 255, .20);
            border-radius: 14px;
            padding: .75rem 1rem;
            background: rgba(18, 9, 30, .76);
        }
        .stButton > button, .stDownloadButton > button {
            border-color: rgba(187, 105, 255, .56);
        }
        [data-testid="stAltairChart"] {
            width: 100%;
            touch-action: manipulation;
        }
        div[data-baseweb="tab-list"] {
            overflow-x: auto;
            flex-wrap: nowrap;
            scrollbar-width: thin;
        }
        button[data-baseweb="tab"] { white-space: nowrap; }
        @media (max-width: 700px) {
            [data-testid="stMainBlockContainer"] {
                padding: .7rem .65rem 4rem;
            }
            h1 { font-size: 1.7rem !important; line-height: 1.12 !important; }
            h2 { font-size: 1.4rem !important; }
            h3 { font-size: 1.15rem !important; }
            p, label, [data-testid="stCaptionContainer"] {
                line-height: 1.35;
            }
            .stButton > button, .stDownloadButton > button {
                min-height: 3rem;
                width: 100%;
                padding: .65rem .75rem;
                font-size: 1rem;
                touch-action: manipulation;
            }
            div[data-baseweb="select"] > div,
            div[data-baseweb="input"] > div,
            .stTextInput input,
            .stNumberInput input {
                min-height: 3rem;
                font-size: 16px !important;
            }
            div[data-testid="stMetric"] {
                padding: .6rem .7rem;
            }
            div[data-testid="stHorizontalBlock"] {
                gap: .55rem;
            }
            [data-testid="stAltairChart"] > div {
                max-width: 100% !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_roster_editor() -> None:
    st.title("Herren 1 · Kader")
    st.caption("Gemeinsamer Herren-1-Kader für diese Matchanalyse.")
    players = load_roster(BASE_DIR)
    edited = st.data_editor(
        roster_rows(players),
        num_rows="dynamic",
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": st.column_config.TextColumn("Interne ID", disabled=True),
            "name": st.column_config.TextColumn("Name", required=True),
            "primary_position": st.column_config.SelectboxColumn(
                "Hauptposition",
                options=list(POSITION_OPTIONS),
                format_func=lambda value: POSITION_LABELS.get(value, value),
                required=True,
            ),
            "secondary_positions": st.column_config.TextColumn(
                "Nebenpositionen",
                help="Optional, mit Komma trennen: outside, middle, opposite, setter, libero",
            ),
            "backup_setter": st.column_config.CheckboxColumn("Kann Pass spielen"),
        },
        key="h1_roster_editor",
    )
    left, right = st.columns([1, 1])
    if left.button("Kader speichern", type="primary", use_container_width=True):
        rows = edited.to_dict("records") if hasattr(edited, "to_dict") else list(edited)
        new_players = players_from_rows(rows)
        if not new_players:
            st.error("Mindestens ein Spieler mit Namen ist erforderlich.")
        else:
            save_roster(BASE_DIR, new_players)
            st.success("Kader gespeichert.")
            if not has_complete_lineup(new_players):
                st.warning(
                    "Für ein Match werden mindestens 1 Zuspieler, 1 Diagonalspieler, "
                    "2 Aussen, 2 Mitten und 1 Libero benötigt."
                )
    if right.button("Generischen Startkader wiederherstellen", use_container_width=True):
        save_roster(BASE_DIR, DEFAULT_ROSTER)
        st.session_state.pop("h1_roster_editor", None)
        st.rerun()

    st.info(
        "Bestehende Matches speichern stabile Spieler-IDs. Namen und Positionen deshalb möglichst "
        "vor dem ersten Match festlegen; spätere Änderungen beeinflussen alte Matchdaten nicht."
    )


def main() -> None:
    configure_page()
    init_db()
    st.sidebar.markdown("## VBC Frauenfeld")
    st.sidebar.caption(f"Herren 1 · Matchanalyse · Datenbank v{SCHEMA_VERSION}")
    page = st.sidebar.radio("Bereich", ("Matchanalyse", "Kader"), label_visibility="collapsed")
    st.sidebar.divider()
    st.sidebar.caption("Eigenständige Streamlit-App · getrennt vom alten Volleyball-Projekt")

    if page == "Kader":
        render_roster_editor()
        return

    roster = load_roster(BASE_DIR)
    if not has_complete_lineup(roster):
        st.warning(
            "Der Kader ist noch nicht vollständig. Öffne links **Kader** und erfasse mindestens "
            "1 Zuspieler, 1 Diagonalspieler, 2 Aussen, 2 Mitten und 1 Libero."
        )
    st.title("Herren 1 · Matchanalyse")
    st.caption(
        "Live erfassen, Video nachbearbeiten und Matchdaten bis zur Spieler-PDF auswerten. "
        "Die App kann vom ganzen Herren-1-Team gemeinsam genutzt werden."
    )
    render_match_analysis(roster, player_label)


if __name__ == "__main__":
    main()
