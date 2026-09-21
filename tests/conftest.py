"""Enable real Home Assistant fixtures for the custom integration."""

import pytest

from .test_telemetry import raw  # noqa: F401


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Permit loading the local custom_components package."""
