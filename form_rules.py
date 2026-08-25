"""Central, coach-adjustable rules for the match form scale.

All thresholds that define how the coloured form bars move live in this file.
Changing ``FORM_SCALE_RULES`` is therefore enough to tune the model later.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class FormScaleRules:
    """The small set of coaching decisions behind every form transition."""

    minimum_level: int = 0
    maximum_level: int = 5
    initial_level: int = 3
    yellow_level: int = 3
    level_colors: tuple[str, ...] = (
        "#B42318",
        "#D85A4F",
        "#E89A59",
        "#EBCB66",
        "#8DBA68",
        "#2E8B57",
    )
    reception_positive: frozenset[str] = frozenset({"perfect", "good"})
    reception_conditional: str = "okay"
    reception_negative: frozenset[str] = frozenset({"bad", "error"})
    reception_error_rating: str = "error"
    service_reception_perfect_level: int = 4
    service_reception_too_low_threshold: int = 2
    defense_reception_too_low_threshold: int = 3
    communication_error_threshold: int = 2
    reset_communication_counter_each_set: bool = False
    setter_fast_streak: int = 2
    reset_setter_fast_streak_each_set: bool = False
    set_location_nonoptimal_high_threshold: int = 3
    set_location_nonoptimal_other_threshold: int = 5
    set_location_error_drop: int = 1
    reset_set_location_counter_each_set: bool = False
    reset_reception_too_low_each_set: bool = False
    missed_team_attacks_above_yellow: int = 6
    missed_team_attacks_at_or_below_yellow: int = 10
    reset_missed_team_attacks_each_set: bool = True
    block_middle_late_threshold: int = 5
    block_closed_threshold: int = 7
    block_touch_threshold: int = 5
    reset_block_counters_each_set: bool = False
    service_ace_rating: str = "ace"
    service_immediate_positive: frozenset[str] = frozenset({"very_good"})
    service_good_rating: str = "good"
    service_good_streak: int = 3
    service_okay_rating: str = "okay"
    service_okay_streak: int = 3
    service_error_rating: str = "error"
    service_jump_error_protection_after: int = 3
    reset_service_streaks_each_set: bool = False

    def __post_init__(self) -> None:
        if not self.minimum_level <= self.yellow_level <= self.maximum_level:
            raise ValueError("yellow_level must be inside the form scale")
        if not self.minimum_level <= self.initial_level <= self.maximum_level:
            raise ValueError("initial_level must be inside the form scale")
        if len(self.level_colors) != self.maximum_level - self.minimum_level + 1:
            raise ValueError("level_colors must contain one colour for every form level")
        if self.missed_team_attacks_above_yellow < 1:
            raise ValueError("missed_team_attacks_above_yellow must be positive")
        if self.missed_team_attacks_at_or_below_yellow < 1:
            raise ValueError("missed_team_attacks_at_or_below_yellow must be positive")
        if self.block_middle_late_threshold < 1:
            raise ValueError("block_middle_late_threshold must be positive")
        if self.block_closed_threshold < 1:
            raise ValueError("block_closed_threshold must be positive")
        if self.block_touch_threshold < 1:
            raise ValueError("block_touch_threshold must be positive")
        if self.service_good_streak < 1:
            raise ValueError("service_good_streak must be positive")
        if self.service_okay_streak < 1:
            raise ValueError("service_okay_streak must be positive")
        if self.service_jump_error_protection_after < 1:
            raise ValueError("service_jump_error_protection_after must be positive")
        if not self.minimum_level <= self.service_reception_perfect_level <= self.maximum_level:
            raise ValueError("service_reception_perfect_level must be inside the form scale")
        if self.service_reception_too_low_threshold < 1:
            raise ValueError("service_reception_too_low_threshold must be positive")
        if self.defense_reception_too_low_threshold < 1:
            raise ValueError("defense_reception_too_low_threshold must be positive")
        if self.communication_error_threshold < 1:
            raise ValueError("communication_error_threshold must be positive")
        if self.setter_fast_streak < 1:
            raise ValueError("setter_fast_streak must be positive")
        if self.set_location_nonoptimal_high_threshold < 1:
            raise ValueError("set_location_nonoptimal_high_threshold must be positive")
        if self.set_location_nonoptimal_other_threshold < 1:
            raise ValueError("set_location_nonoptimal_other_threshold must be positive")
        if self.set_location_error_drop < 1:
            raise ValueError("set_location_error_drop must be positive")


# Zentrale Stellschrauben für die Formskala. Diese Werte können später ohne
# Änderungen an Matchanalyse, Webansicht oder PDF angepasst werden.
FORM_SCALE_RULES = FormScaleRules()


def clamp_form_level(level: int, rules: FormScaleRules = FORM_SCALE_RULES) -> int:
    """Keep a form level within the configured colour scale."""

    return max(rules.minimum_level, min(rules.maximum_level, int(level)))


def reception_level_change(
    quality: str,
    current_level: int,
    *,
    service_reception: bool = False,
    freeball: bool = False,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> int:
    """Return the reception step for one rated first contact."""

    if service_reception and quality == "perfect":
        if current_level == rules.service_reception_perfect_level:
            return 1
        if current_level >= rules.maximum_level:
            return 0
        return rules.service_reception_perfect_level - current_level
    if freeball and quality == "perfect" and current_level < rules.yellow_level:
        return 2
    if quality in rules.reception_positive:
        return 1
    if quality == rules.reception_conditional:
        return -1 if current_level > rules.yellow_level else 0
    if quality == rules.reception_error_rating:
        return rules.minimum_level - current_level if current_level <= 1 else 1 - current_level
    if quality in rules.reception_negative:
        return -1
    return 0


RECEPTION_FORM_METRICS = frozenset({"serve_reception", "defense_reception"})


def reception_form_update(
    current_level: int,
    quality: str,
    *,
    service_reception: bool = False,
    too_low: bool = False,
    freeball: bool = False,
    too_low_count: int = 0,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> tuple[int, int]:
    """Apply one first contact and return its level plus the running low-ball count."""

    current_level = clamp_form_level(
        current_level
        + reception_level_change(
            quality,
            current_level,
            service_reception=service_reception,
            rules=rules,
            freeball=freeball,
        ),
        rules,
    )
    if too_low:
        too_low_count += 1
        too_low_threshold = (
            rules.service_reception_too_low_threshold
            if service_reception
            else rules.defense_reception_too_low_threshold
        )
        if too_low_count >= too_low_threshold:
            current_level = clamp_form_level(current_level - 1, rules)
            too_low_count = 0
    return current_level, too_low_count


def communication_form_update(
    current_level: int,
    communication_count: int = 0,
    *,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> tuple[int, int]:
    """Lower reception form after the configured number of communication errors."""

    communication_count += 1
    if communication_count >= rules.communication_error_threshold:
        return clamp_form_level(current_level - 1, rules), 0
    return clamp_form_level(current_level, rules), communication_count


def level_after_action(
    current_level: int,
    metric: str,
    *,
    signal: int = 0,
    rating: str = "",
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> int:
    """Apply a rated action to the current form level."""

    step = (
        reception_level_change(
            rating,
            current_level,
            service_reception=metric == "serve_reception",
            rules=rules,
        )
        if metric in RECEPTION_FORM_METRICS or metric == "reception"
        else max(-1, min(1, int(signal)))
    )
    return clamp_form_level(current_level + step, rules)


def missed_attack_threshold(
    current_level: int,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> int:
    """Return missed team attacks required for the next attack-form drop."""

    if current_level > rules.yellow_level:
        return rules.missed_team_attacks_above_yellow
    return rules.missed_team_attacks_at_or_below_yellow


def block_form_update(
    current_level: int,
    *,
    block_result: str = "",
    block_formation: str = "",
    middle_late_count: int = 0,
    closed_count: int = 0,
    touch_count: int = 0,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> tuple[int, int, int, int]:
    """Apply one middle-block action and keep the three match-long counters."""

    direct_change = 0
    counter_change = 0
    if block_result == "point":
        direct_change = 1
    elif block_result == "error":
        direct_change = -1

    if block_formation == "middle_late":
        middle_late_count += 1
        if middle_late_count >= rules.block_middle_late_threshold:
            counter_change -= 1
            middle_late_count = 0
    elif block_formation == "closed":
        closed_count += 1
        if closed_count >= rules.block_closed_threshold:
            counter_change += 1
            closed_count = 0

    if block_result == "touch":
        touch_count += 1
        if touch_count >= rules.block_touch_threshold:
            counter_change += 1
            touch_count = 0

    level_change = direct_change or max(-1, min(1, counter_change))
    return (
        clamp_form_level(current_level + level_change, rules),
        middle_late_count,
        closed_count,
        touch_count,
    )


def setter_movement_form_update(
    current_level: int,
    movement: str,
    *,
    fast_streak: int = 0,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> tuple[int, int]:
    """Reward two consecutive timely movements; penalise a late movement immediately."""

    if movement == "fast":
        fast_streak += 1
        if fast_streak >= rules.setter_fast_streak:
            return clamp_form_level(current_level + 1, rules), 0
        return clamp_form_level(current_level, rules), fast_streak
    if movement == "late":
        return clamp_form_level(current_level - 1, rules), 0
    return clamp_form_level(current_level, rules), fast_streak


def set_location_form_update(
    current_level: int,
    *,
    optimal: bool,
    error: bool = False,
    nonoptimal_count: int = 0,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> tuple[int, int]:
    """Update the setter's pass-location form for one technically rated set."""

    if error:
        return clamp_form_level(current_level - rules.set_location_error_drop, rules), 0
    if optimal:
        return clamp_form_level(current_level + 1, rules), nonoptimal_count

    nonoptimal_count += 1
    threshold = (
        rules.set_location_nonoptimal_high_threshold
        if current_level > rules.yellow_level
        else rules.set_location_nonoptimal_other_threshold
    )
    if nonoptimal_count >= threshold:
        return clamp_form_level(current_level - 1, rules), 0
    return clamp_form_level(current_level, rules), nonoptimal_count


def service_form_update(
    current_level: int,
    result: str,
    *,
    service_type: str = "",
    good_streak: int = 0,
    okay_streak: int = 0,
    previous_errors: int = 0,
    error_free_services: int = 0,
    rules: FormScaleRules = FORM_SCALE_RULES,
) -> tuple[int, int, int, int, int]:
    """Apply one personal service and return level plus all running counters."""

    normalized_result = "okay" if result == "in_play" else result
    if normalized_result == rules.service_error_rating:
        protected_jump_error = (
            service_type == "jump" and error_free_services >= rules.service_jump_error_protection_after
        )
        if protected_jump_error:
            next_level = clamp_form_level(current_level - 1, rules)
        elif previous_errors > 0 or current_level <= 1:
            next_level = rules.minimum_level
        else:
            next_level = 1
        return next_level, 0, 0, previous_errors + 1, 0

    if normalized_result not in {
        rules.service_ace_rating,
        *rules.service_immediate_positive,
        rules.service_good_rating,
        rules.service_okay_rating,
    }:
        return (
            clamp_form_level(current_level, rules),
            good_streak,
            okay_streak,
            previous_errors,
            error_free_services,
        )

    error_free_services += 1
    if normalized_result == rules.service_ace_rating:
        return rules.maximum_level, 0, 0, previous_errors, error_free_services
    if normalized_result in rules.service_immediate_positive:
        return (
            clamp_form_level(current_level + 1, rules),
            0,
            0,
            previous_errors,
            error_free_services,
        )
    if normalized_result == rules.service_good_rating:
        good_streak += 1
        if good_streak >= rules.service_good_streak:
            return (
                clamp_form_level(current_level + 1, rules),
                0,
                0,
                previous_errors,
                error_free_services,
            )
        return (
            clamp_form_level(current_level, rules),
            good_streak,
            0,
            previous_errors,
            error_free_services,
        )

    okay_streak += 1
    if okay_streak >= rules.service_okay_streak:
        if current_level > rules.yellow_level:
            current_level -= 1
        elif current_level < rules.yellow_level:
            current_level += 1
        okay_streak = 0
    return (
        clamp_form_level(current_level, rules),
        0,
        okay_streak,
        previous_errors,
        error_free_services,
    )
