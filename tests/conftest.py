"""Configuration pytest pour les tests du SDK Client."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Use asyncio as the default async backend."""
    return "asyncio"
