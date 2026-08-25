from __future__ import annotations

from typing import Any


BUNDLED_CONTENT_MARKER = "bundled_coach_data_public_empty_v1"


def load_bundled_coach_data() -> dict[str, list[dict[str, Any]]]:
    """Return an empty public bundle; private coaching content is not published."""

    return {"custom_exercises": [], "custom_training_plans": []}
