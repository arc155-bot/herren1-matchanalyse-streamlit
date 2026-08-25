from __future__ import annotations


WOMENS_NET_TOP_METERS = 2.24
VOLLEYBALL_NET_HEIGHT_METERS = 1.0
WOMENS_NET_BOTTOM_METERS = 1.24
NET_MESH_ROWS = 10
NET_MESH_COLUMNS = 18


def net_mesh_heights() -> tuple[float, ...]:
    """Return the inner horizontal mesh lines of a regulation one-metre net."""

    return tuple(
        WOMENS_NET_BOTTOM_METERS
        + VOLLEYBALL_NET_HEIGHT_METERS * row / NET_MESH_ROWS
        for row in range(1, NET_MESH_ROWS)
    )


def net_mesh_positions() -> tuple[float, ...]:
    """Return half-metre mesh columns across a nine-metre court."""

    return tuple(9.0 * column / NET_MESH_COLUMNS for column in range(1, NET_MESH_COLUMNS))
