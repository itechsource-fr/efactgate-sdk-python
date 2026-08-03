"""Flux status data models for the Orwin SDK Client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from orwin_sdk.models.enums import FluxStatus, FluxType


@dataclass(frozen=True, slots=True)
class TransitionDetail:
    """Represents a single status transition in a flux lifecycle.

    Attributes:
        from_status: The status before the transition.
        to_status: The status after the transition.
        reason: Description of why the transition occurred.
        transitioned_at: UTC timestamp of the transition.
    """

    from_status: FluxStatus
    to_status: FluxStatus
    reason: str
    transitioned_at: datetime


@dataclass(frozen=True, slots=True)
class FluxStatusResponse:
    """Response returned when querying a flux status.

    Attributes:
        flux_id: Unique identifier of the flux.
        status: Current status of the flux.
        flux_type: Type of the flux.
        submitted_at: UTC timestamp of original submission.
        transitions: Ordered list of status transitions.
    """

    flux_id: UUID
    status: FluxStatus
    flux_type: FluxType
    submitted_at: datetime
    transitions: list[TransitionDetail] = field(default_factory=list)


__all__ = [
    "FluxStatusResponse",
    "TransitionDetail",
]
