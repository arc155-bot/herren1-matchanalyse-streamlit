from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Exercise:
    id: str
    title: str
    goal: str
    setup: str


def default_exercises() -> list[Exercise]:
    """Small match-analysis exercise library used by the recommendation panel."""

    return [
        Exercise(
            "sideout_three_balls",
            "Sideout: drei erste Bälle",
            "Den ersten Angriff nach gegnerischem Service stabil und punktgefährlich lösen.",
            "Drei definierte Aufschläge pro Rotation; Punkt zählt nur bei erfolgreichem Sideout.",
        ),
        Exercise(
            "sideout_rotation_ladder",
            "Sideout-Läuferleiter",
            "Schwache Läufer gezielt unter Annahmedruck trainieren.",
            "Läufer nacheinander spielen; nach zwei Sideouts wird weiterrotiert.",
        ),
        Exercise(
            "sv_zone_serve",
            "Zonenservice unter Druck",
            "Aufschlagfehler senken und Zielzonen verlässlich treffen.",
            "Neun Ein-Meter-Zonen markieren und mit Treffer- sowie Fehlerpunkten spielen.",
        ),
        Exercise(
            "serve_pressure_finish",
            "Service-Druckphase",
            "Breakpoint-Chancen mit mutigem, kontrolliertem Service erhöhen.",
            "Kurze Sätze beginnen bei 20:20; der Aufschläger bleibt bis zum Sideout.",
        ),
        Exercise(
            "attack_toolbox",
            "Angriffs-Werkzeugkasten",
            "Punktquote durch Linie, Diagonal, Block-out und kontrollierte Lösungen verbessern.",
            "Angreifer müssen in einer Serie mindestens drei unterschiedliche Lösungen zeigen.",
        ),
        Exercise(
            "k1_wash_two_solutions",
            "K1-Wash mit zwei Lösungen",
            "Aus guten Zuspielen konsequent punkten und aus schlechten Bällen im Rally bleiben.",
            "Sideout-Team braucht zwei Punkte in Folge; Abwehrteam einen Breakpoint zum Ausgleich.",
        ),
        Exercise(
            "transition_first_three_steps",
            "Transition: erste drei Schritte",
            "Nach Block oder Abwehr schneller wieder anspielbar werden.",
            "Block-Abwehr-Angriff in kurzen Wiederholungen mit Fokus auf die ersten drei Schritte.",
        ),
        Exercise(
            "setter_choice_three_front",
            "Zuspielwahl mit drei Angreifern",
            "Tempo und Verteilung bei stabiler Annahme verbessern.",
            "Drei Angriffsoptionen vorne; Bonus für früh getroffene und erfolgreiche Entscheidungen.",
        ),
        Exercise(
            "middle_block_transition",
            "Mitte: Block zu Angriff",
            "Blockqualität und anschließende Angriffsbeteiligung verbinden.",
            "Mittelblocker pendeln zwischen zwei Blockpositionen und lösen sofort zum Angriff.",
        ),
        Exercise(
            "vc_team_block",
            "Teamblock und Feldabwehr",
            "Blockrichtung und Abwehrposition gemeinsam stabilisieren.",
            "Block gibt Linie oder Diagonal vor; Abwehr startet passend und kontert kontrolliert.",
        ),
    ]
