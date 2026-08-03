"""Acknowledgement response model for the Efactgate SDK Client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID


@dataclass(frozen=True, slots=True)
class AckResponse:
    """Acknowledgement response from a target connector.

    Attributes:
        flux_id: Unique identifier of the acknowledged flux.
        ack_payload: Structured payload of the acknowledgement.
        received_at: UTC timestamp when the ACK was received.
    """

    flux_id: UUID
    ack_payload: dict[str, Any]
    received_at: datetime


__all__ = [
    "AckResponse",
]
