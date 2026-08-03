"""Property-based tests for JSON serialization round-trip.

Tests validate:
- Property 1: Round-trip JSON pour tous les modèles SDK
- Property 2: Conformité des formats de sérialisation
- Property 3: Champs optionnels et inconnus
- Property 4: Désérialisation invalide lève DeserializationError

Validates: Requirements 12.1, 12.2, 12.3, 12.4, 12.5, 4.6
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from efactgate_sdk.exceptions import DeserializationError
from efactgate_sdk.models.ack import AckResponse
from efactgate_sdk.models.enums import FluxStatus, FluxType, InvoiceFormat
from efactgate_sdk.models.ereporting import EReportingSubmission
from efactgate_sdk.models.invoice import (
    FluxCreatedResponse,
    ImportErrorDetail,
    ImportReport,
    InvoiceSubmission,
)
from efactgate_sdk.models.status import FluxStatusResponse, TransitionDetail
from efactgate_sdk.transport.serialization import deserialize, serialize

# ---------------------------------------------------------------------------
# Hypothesis strategies
# ---------------------------------------------------------------------------

# Datetimes with UTC timezone, constrained to avoid edge cases with extremes
utc_datetime_st = st.datetimes(
    min_value=datetime(2000, 1, 1),
    max_value=datetime(2100, 12, 31),
    timezones=st.just(UTC),
)

uuid_st = st.uuids()

# Safe text that won't break JSON encoding
safe_text_st = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "Z"),
        blacklist_characters=("\x00",),
    ),
    min_size=1,
    max_size=50,
)

# Metadata: optional dict of string→string
metadata_st = st.one_of(
    st.none(),
    st.dictionaries(
        keys=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N")),
            min_size=1,
            max_size=20,
        ),
        values=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
            min_size=0,
            max_size=50,
        ),
        min_size=0,
        max_size=5,
    ),
)

# Ack payload: dict[str, Any] with simple JSON-serializable values
ack_payload_st = st.dictionaries(
    keys=st.text(
        alphabet=st.characters(whitelist_categories=("L", "N")),
        min_size=1,
        max_size=15,
    ),
    values=st.one_of(
        st.text(min_size=0, max_size=30),
        st.integers(min_value=-1000, max_value=1000),
        st.booleans(),
    ),
    min_size=1,
    max_size=5,
)

flux_status_st = st.sampled_from(list(FluxStatus))
flux_type_st = st.sampled_from(list(FluxType))
invoice_format_st = st.sampled_from(list(InvoiceFormat))

# 14-digit SIRET (simplified, not Luhn-validated for strategy purposes)
siret_st = st.text(
    alphabet=st.characters(whitelist_categories=("Nd",)),
    min_size=14,
    max_size=14,
)


# --- Composite strategies for models ---


@st.composite
def flux_created_response_st(draw: st.DrawFn) -> FluxCreatedResponse:
    return FluxCreatedResponse(
        flux_id=draw(uuid_st),
        status=draw(flux_status_st),
        submitted_at=draw(utc_datetime_st),
    )


@st.composite
def transition_detail_st(draw: st.DrawFn) -> TransitionDetail:
    return TransitionDetail(
        from_status=draw(flux_status_st),
        to_status=draw(flux_status_st),
        reason=draw(safe_text_st),
        transitioned_at=draw(utc_datetime_st),
    )


@st.composite
def flux_status_response_st(draw: st.DrawFn) -> FluxStatusResponse:
    return FluxStatusResponse(
        flux_id=draw(uuid_st),
        status=draw(flux_status_st),
        flux_type=draw(flux_type_st),
        submitted_at=draw(utc_datetime_st),
        transitions=draw(st.lists(transition_detail_st(), min_size=0, max_size=5)),
    )


@st.composite
def ack_response_st(draw: st.DrawFn) -> AckResponse:
    return AckResponse(
        flux_id=draw(uuid_st),
        ack_payload=draw(ack_payload_st),
        received_at=draw(utc_datetime_st),
    )


@st.composite
def invoice_submission_st(draw: st.DrawFn) -> InvoiceSubmission:
    return InvoiceSubmission(
        content=draw(safe_text_st),
        format=draw(invoice_format_st),
        target_connector_id=draw(safe_text_st),
        enterprise_siret=draw(siret_st),
        flux_type=draw(flux_type_st),
        metadata=draw(metadata_st),
    )


@st.composite
def import_error_detail_st(draw: st.DrawFn) -> ImportErrorDetail:
    return ImportErrorDetail(
        line_or_section=draw(safe_text_st),
        code=draw(safe_text_st),
        message=draw(safe_text_st),
    )


@st.composite
def import_report_st(draw: st.DrawFn) -> ImportReport:
    return ImportReport(
        total_created=draw(st.integers(min_value=0, max_value=1000)),
        total_errors=draw(st.integers(min_value=0, max_value=100)),
        errors=draw(st.lists(import_error_detail_st(), min_size=0, max_size=5)),
    )


@st.composite
def ereporting_submission_st(draw: st.DrawFn) -> EReportingSubmission:
    return EReportingSubmission(
        content=draw(safe_text_st),
        format=draw(invoice_format_st),
        enterprise_siret=draw(siret_st),
        metadata=draw(metadata_st),
    )


# ---------------------------------------------------------------------------
# Property 1: Round-trip JSON pour tous les modèles SDK
# ---------------------------------------------------------------------------


class TestProperty1RoundTrip:
    """Property 1: Round-trip JSON pour tous les modèles SDK.

    serialize then deserialize produces identical objects.

    **Validates: Requirements 12.1, 4.6**
    """

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=flux_created_response_st())
    def test_roundtrip_flux_created_response(self, model: FluxCreatedResponse) -> None:
        """FluxCreatedResponse survives JSON round-trip."""
        json_str = serialize(model)
        restored = deserialize(json_str, FluxCreatedResponse)
        assert restored == model

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=flux_status_response_st())
    def test_roundtrip_flux_status_response(self, model: FluxStatusResponse) -> None:
        """FluxStatusResponse (with nested TransitionDetail) survives JSON round-trip."""
        json_str = serialize(model)
        restored = deserialize(json_str, FluxStatusResponse)
        assert restored == model

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=ack_response_st())
    def test_roundtrip_ack_response(self, model: AckResponse) -> None:
        """AckResponse survives JSON round-trip."""
        json_str = serialize(model)
        restored = deserialize(json_str, AckResponse)
        assert restored == model

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=invoice_submission_st())
    def test_roundtrip_invoice_submission(self, model: InvoiceSubmission) -> None:
        """InvoiceSubmission survives JSON round-trip."""
        json_str = serialize(model)
        restored = deserialize(json_str, InvoiceSubmission)
        assert restored == model

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=import_report_st())
    def test_roundtrip_import_report(self, model: ImportReport) -> None:
        """ImportReport (with nested ImportErrorDetail) survives JSON round-trip."""
        json_str = serialize(model)
        restored = deserialize(json_str, ImportReport)
        assert restored == model

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=ereporting_submission_st())
    def test_roundtrip_ereporting_submission(self, model: EReportingSubmission) -> None:
        """EReportingSubmission survives JSON round-trip."""
        json_str = serialize(model)
        restored = deserialize(json_str, EReportingSubmission)
        assert restored == model


# ---------------------------------------------------------------------------
# Property 2: Conformité des formats de sérialisation
# ---------------------------------------------------------------------------


# ISO 8601 with Z suffix pattern
ISO_8601_Z_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)

# UUID lowercase canonical pattern (8-4-4-4-12)
UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class TestProperty2SerializationFormats:
    """Property 2: Conformité des formats de sérialisation.

    dates→ISO 8601 Z, UUID→lowercase, Decimal→string, Enum→value.

    **Validates: Requirements 12.2**
    """

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=flux_created_response_st())
    def test_datetime_iso8601_z_suffix(self, model: FluxCreatedResponse) -> None:
        """Datetime fields are serialized as ISO 8601 with Z suffix."""
        json_str = serialize(model)
        data = json.loads(json_str)
        assert ISO_8601_Z_PATTERN.match(data["submitted_at"]), (
            f"Expected ISO 8601 Z format, got: {data['submitted_at']}"
        )

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=flux_created_response_st())
    def test_uuid_lowercase_canonical(self, model: FluxCreatedResponse) -> None:
        """UUID fields are serialized as lowercase canonical 8-4-4-4-12."""
        json_str = serialize(model)
        data = json.loads(json_str)
        assert UUID_PATTERN.match(data["flux_id"]), (
            f"Expected lowercase UUID, got: {data['flux_id']}"
        )

    @pytest.mark.property
    @settings(max_examples=150)
    @given(status=flux_status_st)
    def test_enum_serialized_as_value(self, status: FluxStatus) -> None:
        """Enum fields are serialized as their string value, not 'ClassName.member'."""
        model = FluxCreatedResponse(
            flux_id=UUID("12345678-1234-1234-1234-123456789abc"),
            status=status,
            submitted_at=datetime(2024, 6, 15, 10, 30, 0, tzinfo=UTC),
        )
        json_str = serialize(model)
        data = json.loads(json_str)
        # Value must be the raw enum value (e.g. "emis"), not "FluxStatus.emis"
        assert data["status"] == status.value
        assert "." not in data["status"]

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=flux_status_response_st())
    def test_nested_datetime_format(self, model: FluxStatusResponse) -> None:
        """Nested TransitionDetail datetime fields use ISO 8601 Z format."""
        json_str = serialize(model)
        data = json.loads(json_str)
        for transition in data["transitions"]:
            assert ISO_8601_Z_PATTERN.match(transition["transitioned_at"]), (
                f"Expected ISO 8601 Z, got: {transition['transitioned_at']}"
            )


# ---------------------------------------------------------------------------
# Property 3: Champs optionnels et inconnus
# ---------------------------------------------------------------------------


class TestProperty3OptionalAndUnknownFields:
    """Property 3: Champs optionnels et inconnus.

    absent optional→None, unknown fields ignored.

    **Validates: Requirements 12.3, 12.4**
    """

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=invoice_submission_st())
    def test_absent_optional_produces_none(self, model: InvoiceSubmission) -> None:
        """When an optional field is absent from JSON, deserialization yields None."""
        json_str = serialize(model)
        data = json.loads(json_str)
        # Remove the optional 'metadata' field
        data.pop("metadata", None)
        json_without_optional = json.dumps(data)
        restored = deserialize(json_without_optional, InvoiceSubmission)
        assert restored.metadata is None

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        model=flux_created_response_st(),
        extra_key=st.text(
            alphabet=st.characters(whitelist_categories=("L",)),
            min_size=1,
            max_size=20,
        ),
        extra_value=st.text(min_size=0, max_size=30),
    )
    def test_unknown_fields_are_ignored(
        self, model: FluxCreatedResponse, extra_key: str, extra_value: str
    ) -> None:
        """Unknown fields in JSON are silently ignored during deserialization."""
        json_str = serialize(model)
        data = json.loads(json_str)
        # Inject unknown field (avoid clashing with real field names)
        unknown_key = f"_unknown_{extra_key}"
        data[unknown_key] = extra_value
        json_with_unknown = json.dumps(data)
        restored = deserialize(json_with_unknown, FluxCreatedResponse)
        assert restored == model

    @pytest.mark.property
    @settings(max_examples=150)
    @given(model=ereporting_submission_st())
    def test_absent_optional_ereporting_metadata(
        self, model: EReportingSubmission
    ) -> None:
        """EReportingSubmission optional metadata absent → None."""
        json_str = serialize(model)
        data = json.loads(json_str)
        data.pop("metadata", None)
        json_without_optional = json.dumps(data)
        restored = deserialize(json_without_optional, EReportingSubmission)
        assert restored.metadata is None

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        model=flux_status_response_st(),
        extra_key=st.text(
            alphabet=st.characters(whitelist_categories=("L",)),
            min_size=1,
            max_size=20,
        ),
    )
    def test_unknown_fields_not_preserved_on_reserialize(
        self, model: FluxStatusResponse, extra_key: str
    ) -> None:
        """Unknown fields are dropped on re-serialization (not preserved)."""
        json_str = serialize(model)
        data = json.loads(json_str)
        unknown_key = f"_extra_{extra_key}"
        data[unknown_key] = "should_be_dropped"
        json_with_unknown = json.dumps(data)
        restored = deserialize(json_with_unknown, FluxStatusResponse)
        reserialized = serialize(restored)
        reserialized_data = json.loads(reserialized)
        assert unknown_key not in reserialized_data


# ---------------------------------------------------------------------------
# Property 4: Désérialisation invalide lève DeserializationError
# ---------------------------------------------------------------------------


class TestProperty4DeserializationErrors:
    """Property 4: Désérialisation invalide lève DeserializationError.

    invalid JSON, missing fields, wrong types.

    **Validates: Requirements 12.5**
    """

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        invalid_json=st.text(min_size=1, max_size=100).filter(
            lambda s: _is_invalid_json(s)
        )
    )
    def test_invalid_json_raises(self, invalid_json: str) -> None:
        """Syntactically invalid JSON raises DeserializationError."""
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(invalid_json, FluxCreatedResponse)
        assert exc_info.value.code == "deserialization_error"

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        # Generate valid FluxCreatedResponse JSON but remove a required field
        model=flux_created_response_st(),
        field_to_remove=st.sampled_from(["flux_id", "status", "submitted_at"]),
    )
    def test_missing_required_field_raises(
        self, model: FluxCreatedResponse, field_to_remove: str
    ) -> None:
        """Missing a required field raises DeserializationError."""
        json_str = serialize(model)
        data = json.loads(json_str)
        del data[field_to_remove]
        incomplete_json = json.dumps(data)
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(incomplete_json, FluxCreatedResponse)
        assert exc_info.value.reason == "missing_field"

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        model=flux_created_response_st(),
        wrong_value=st.one_of(
            st.integers(min_value=-100, max_value=100),
            st.booleans(),
            st.lists(st.integers(), min_size=0, max_size=3),
        ),
    )
    def test_wrong_type_for_uuid_raises(
        self, model: FluxCreatedResponse, wrong_value: Any
    ) -> None:
        """Wrong type for UUID field raises DeserializationError."""
        json_str = serialize(model)
        data = json.loads(json_str)
        data["flux_id"] = wrong_value
        bad_json = json.dumps(data)
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(bad_json, FluxCreatedResponse)
        assert exc_info.value.code == "deserialization_error"

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        model=flux_created_response_st(),
        wrong_value=st.one_of(
            st.integers(min_value=-100, max_value=100),
            st.booleans(),
            st.lists(st.integers(), min_size=0, max_size=3),
        ),
    )
    def test_wrong_type_for_datetime_raises(
        self, model: FluxCreatedResponse, wrong_value: Any
    ) -> None:
        """Wrong type for datetime field raises DeserializationError."""
        json_str = serialize(model)
        data = json.loads(json_str)
        data["submitted_at"] = wrong_value
        bad_json = json.dumps(data)
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(bad_json, FluxCreatedResponse)
        assert exc_info.value.code == "deserialization_error"

    @pytest.mark.property
    @settings(max_examples=150)
    @given(
        model=flux_created_response_st(),
        invalid_enum=st.text(
            alphabet=st.characters(whitelist_categories=("L",)),
            min_size=1,
            max_size=20,
        ).filter(lambda s: s not in [e.value for e in FluxStatus]),
    )
    def test_invalid_enum_value_raises(
        self, model: FluxCreatedResponse, invalid_enum: str
    ) -> None:
        """Invalid enum value raises DeserializationError."""
        json_str = serialize(model)
        data = json.loads(json_str)
        data["status"] = invalid_enum
        bad_json = json.dumps(data)
        with pytest.raises(DeserializationError) as exc_info:
            deserialize(bad_json, FluxCreatedResponse)
        assert exc_info.value.reason == "invalid_enum_value"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_invalid_json(s: str) -> bool:
    """Check if a string is NOT valid JSON."""
    try:
        json.loads(s)
        return False
    except (json.JSONDecodeError, ValueError):
        return True
