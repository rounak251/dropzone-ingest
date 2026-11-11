"""Test hook: send SIGKILL to this process at a chosen point. Off unless DZ_KILL_AT is set."""

import os
import signal

from dropzone.contract import DropError

POINTS = ("mid_batch", "pre_commit", "boundary")


def parse(value: str | None) -> tuple[str, int] | None:
    """Parse '<point>' or '<point>:<batch>'. The batch number counts from 1 in this process."""
    if not value:
        return None
    point, _, batch = value.partition(":")
    if point not in POINTS:
        raise DropError(f"DZ_KILL_AT point must be one of {POINTS}, got {point!r}")
    return point, int(batch) if batch else 1


def fire(armed: tuple[str, int] | None, point: str, batch_no: int) -> None:
    if armed == (point, batch_no):
        os.kill(os.getpid(), signal.SIGKILL)
