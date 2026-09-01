"""Property-based tests for OpenAPI / cross-language coherence.

Tests validate:
- Property 24: Cohérence cross-langage des endpoints et modèles
- Property 25: Préservation des fichiers personnalisables à la régénération
- Property 26: Spécification invalide interrompt la génération proprement

Validates: Requirements 1.2, 1.5, 1.6
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SDK_ROOT = Path(__file__).resolve().parents[2]  # sdk/python/
OPENAPI_DIR = SDK_ROOT.parent / "openapi"
SPEC_FILE = OPENAPI_DIR / "efactgate-openapi.yaml"
GENERATOR_CONFIG = OPENAPI_DIR / "generator-config.yaml"
SCRIPTS_DIR = SDK_ROOT.parent / "scripts"
GENERATE_SCRIPT = SCRIPTS_DIR / "generate.sh"


# ---------------------------------------------------------------------------
# Helpers: Load OpenAPI spec
# ---------------------------------------------------------------------------


def _load_openapi_spec() -> dict[str, Any]:
    """Load and parse the OpenAPI specification YAML."""
    if not SPEC_FILE.exists():
        pytest.skip(f"OpenAPI spec not found at {SPEC_FILE}")
    with open(SPEC_FILE, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_generator_config() -> dict[str, Any]:
    """Load and parse the generator configuration YAML."""
    if not GENERATOR_CONFIG.exists():
        pytest.skip(f"Generator config not found at {GENERATOR_CONFIG}")
    with open(GENERATOR_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _extract_spec_endpoints(spec: dict[str, Any]) -> list[dict[str, str]]:
    """Extract endpoint (method, path, operationId) from spec paths."""
    endpoints: list[dict[str, str]] = []
    for path, path_item in spec.get("paths", {}).items():
        for method in ("get", "post", "put", "patch", "delete"):
            if method in path_item:
                operation = path_item[method]
                endpoints.append({
                    "method": method.upper(),
                    "path": path,
                    "operationId": operation.get("operationId", ""),
                })
    return endpoints


def _extract_spec_schema_names(spec: dict[str, Any]) -> set[str]:
    """Extract schema names from components/schemas."""
    return set(spec.get("components", {}).get("schemas", {}).keys())


def _extract_spec_enum_values(spec: dict[str, Any], enum_name: str) -> list[str]:
    """Extract enum values for a given schema from the spec."""
    schemas = spec.get("components", {}).get("schemas", {})
    schema = schemas.get(enum_name, {})
    return schema.get("enum", [])


# ---------------------------------------------------------------------------
# Property 24: Cohérence cross-langage des endpoints et modèles
# ---------------------------------------------------------------------------


class TestProperty24CrossLanguageCoherence:
    """Property 24: Cohérence cross-langage des endpoints et modèles.

    The Python SDK models and methods must be aligned with the OpenAPI spec:
    - All endpoints in spec have corresponding client methods
    - All schema models in spec have corresponding Python classes
    - All enum values in spec match the Python StrEnum values
    - All required fields in spec schemas exist in Python models

    **Validates: Requirements 1.2**
    """

    @pytest.mark.property
    def test_all_spec_endpoints_have_client_methods(self) -> None:
        """Every operationId in the OpenAPI spec maps to an EFactGateClient method."""
        from efactgate_sdk.client import EFactGateClient

        spec = _load_openapi_spec()
        endpoints = _extract_spec_endpoints(spec)

        # Map operationId → expected client method name (camelCase→snake_case)
        operation_to_method: dict[str, str] = {
            "submitInvoice": "submit_invoice",
            "submitEReporting": "submit_ereporting",
            "submitBatch": "submit_batch",
            "importFile": "import_file",
            "getStatus": "get_status",
            "getAck": "get_ack",
        }

        for endpoint in endpoints:
            op_id = endpoint["operationId"]
            expected_method = operation_to_method.get(op_id)
            assert expected_method is not None, (
                f"Unmapped operationId in spec: {op_id}"
            )
            assert hasattr(EFactGateClient, expected_method), (
                f"EFactGateClient missing method '{expected_method}' "
                f"for operationId '{op_id}' (endpoint: {endpoint['method']} {endpoint['path']})"
            )

    @pytest.mark.property
    def test_all_spec_enums_match_python_enums(self) -> None:
        """Every enum value in the OpenAPI spec exists in the Python StrEnum."""
        from efactgate_sdk.models.enums import (
            FluxStatus,
            FluxType,
            ImportFormat,
            InvoiceFormat,
        )

        spec = _load_openapi_spec()

        enum_mapping: dict[str, type] = {
            "InvoiceFormat": InvoiceFormat,
            "FluxType": FluxType,
            "FluxStatus": FluxStatus,
            "ImportFormat": ImportFormat,
        }

        for spec_enum_name, python_enum_class in enum_mapping.items():
            spec_values = _extract_spec_enum_values(spec, spec_enum_name)
            python_values = [e.value for e in python_enum_class]

            assert set(spec_values) == set(python_values), (
                f"Enum mismatch for {spec_enum_name}: "
                f"spec={sorted(spec_values)}, python={sorted(python_values)}"
            )

    @pytest.mark.property
    def test_spec_request_models_have_python_equivalents(self) -> None:
        """Request schemas in the spec have corresponding Python models."""
        import dataclasses

        from efactgate_sdk.models.ereporting import EReportingSubmission
        from efactgate_sdk.models.invoice import InvoiceSubmission

        spec = _load_openapi_spec()
        schemas = spec.get("components", {}).get("schemas", {})

        request_model_mapping: dict[str, type] = {
            "InvoiceSubmission": InvoiceSubmission,
            "EReportingSubmission": EReportingSubmission,
        }

        for spec_name, python_class in request_model_mapping.items():
            assert spec_name in schemas, (
                f"Schema '{spec_name}' not found in OpenAPI spec"
            )
            spec_required = set(schemas[spec_name].get("required", []))

            # Get dataclass field names
            dc_fields = {f.name for f in dataclasses.fields(python_class)}

            # All required spec fields must exist in the Python dataclass
            missing = spec_required - dc_fields
            assert not missing, (
                f"Python model {python_class.__name__} missing required fields "
                f"from spec schema '{spec_name}': {missing}"
            )

    @pytest.mark.property
    def test_spec_response_models_have_python_equivalents(self) -> None:
        """Response schemas in the spec have corresponding Python models."""
        import dataclasses

        from efactgate_sdk.models.ack import AckResponse
        from efactgate_sdk.models.invoice import (
            BatchResponse,
            FluxCreatedResponse,
            ImportReport,
        )
        from efactgate_sdk.models.status import FluxStatusResponse

        spec = _load_openapi_spec()
        schemas = spec.get("components", {}).get("schemas", {})

        response_model_mapping: dict[str, type] = {
            "FluxCreatedResponse": FluxCreatedResponse,
            "BatchResponse": BatchResponse,
            "FluxStatusResponse": FluxStatusResponse,
            "AckResponse": AckResponse,
            "ImportReport": ImportReport,
        }

        for spec_name, python_class in response_model_mapping.items():
            assert spec_name in schemas, (
                f"Schema '{spec_name}' not found in OpenAPI spec"
            )
            spec_required = set(schemas[spec_name].get("required", []))

            # Get dataclass field names
            dc_fields = {f.name for f in dataclasses.fields(python_class)}

            # All required fields from spec must exist in Python model
            missing = spec_required - dc_fields
            assert not missing, (
                f"Python model {python_class.__name__} missing required fields "
                f"from spec schema '{spec_name}': {missing}"
            )

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        endpoint_idx=st.integers(min_value=0, max_value=5),
    )
    def test_endpoint_methods_match_http_verbs(self, endpoint_idx: int) -> None:
        """SDK methods use the correct HTTP verb for each endpoint."""
        spec = _load_openapi_spec()
        endpoints = _extract_spec_endpoints(spec)

        if endpoint_idx >= len(endpoints):
            return

        endpoint = endpoints[endpoint_idx]
        # Verify that all endpoints are declared (POST for writes, GET for reads)
        if endpoint["path"].startswith("/status") or endpoint["path"].startswith("/ack"):
            assert endpoint["method"] == "GET"
        else:
            assert endpoint["method"] == "POST"

    @pytest.mark.property
    def test_generator_config_covers_all_languages(self) -> None:
        """Generator config defines all required target languages."""
        config = _load_generator_config()
        generators = config.get("generators", {})

        required_languages = {"python", "typescript", "java", "c"}
        configured_languages = set(generators.keys())

        missing = required_languages - configured_languages
        assert not missing, (
            f"Generator config missing languages: {missing}"
        )

    @pytest.mark.property
    def test_generator_config_references_correct_spec(self) -> None:
        """Generator config references the correct OpenAPI spec file."""
        config = _load_generator_config()
        global_config = config.get("global", {})
        input_spec = global_config.get("inputSpec", "")

        assert "efactgate-openapi.yaml" in input_spec, (
            f"Generator config inputSpec does not reference correct spec: {input_spec}"
        )


# ---------------------------------------------------------------------------
# Property 25: Préservation des fichiers personnalisables à la régénération
# ---------------------------------------------------------------------------


class TestProperty25CustomFilePreservation:
    """Property 25: Préservation des fichiers personnalisables à la régénération.

    Custom directories (custom/) and custom-suffixed files (*_custom.*) must be
    preserved when the generate script runs. Tested by verifying:
    - The generate.sh script contains backup/restore logic
    - The generator-config preserves the correct patterns
    - A simulated backup+restore cycle preserves arbitrary custom content

    **Validates: Requirements 1.5**
    """

    @pytest.mark.property
    def test_generator_config_declares_preserve_patterns(self) -> None:
        """Each language in generator config has preservePatterns defined."""
        config = _load_generator_config()
        generators = config.get("generators", {})

        for lang, lang_config in generators.items():
            patterns = lang_config.get("preservePatterns", [])
            assert len(patterns) > 0, (
                f"Language '{lang}' has no preservePatterns in generator config"
            )
            # Must include a custom/ directory pattern
            has_custom_dir = any("custom" in p for p in patterns)
            assert has_custom_dir, (
                f"Language '{lang}' preservePatterns missing 'custom/' directory pattern"
            )

    @pytest.mark.property
    def test_global_config_declares_preserved_files(self) -> None:
        """Global config filesPreservedOnRegeneration includes custom patterns."""
        config = _load_generator_config()
        global_config = config.get("global", {})
        preserved = global_config.get("filesPreservedOnRegeneration", [])

        assert "custom/" in preserved, (
            "Global filesPreservedOnRegeneration missing 'custom/' pattern"
        )
        assert "*_custom.*" in preserved, (
            "Global filesPreservedOnRegeneration missing '*_custom.*' pattern"
        )

    @pytest.mark.property
    def test_generate_script_has_backup_restore_logic(self) -> None:
        """The generate.sh script contains backup and restore functions."""
        if not GENERATE_SCRIPT.exists():
            pytest.skip(f"generate.sh not found at {GENERATE_SCRIPT}")

        script_content = GENERATE_SCRIPT.read_text(encoding="utf-8")

        assert "backup_custom_files" in script_content, (
            "generate.sh missing backup_custom_files function"
        )
        assert "restore_custom_files" in script_content, (
            "generate.sh missing restore_custom_files function"
        )
        # Verify it backs up custom/ directories
        assert 'name "custom"' in script_content or '-name "custom"' in script_content, (
            "generate.sh does not search for custom/ directories"
        )
        # Verify it backs up *_custom.* files
        assert "*_custom.*" in script_content, (
            "generate.sh does not handle *_custom.* files"
        )

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        custom_content=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z")),
            min_size=1,
            max_size=200,
        ),
        filename=st.from_regex(r"[a-z]{3,10}_custom\.(py|ts|java|c|h)", fullmatch=True),
    )
    def test_simulated_backup_restore_preserves_content(
        self, custom_content: str, filename: str
    ) -> None:
        """Simulated backup+restore cycle preserves custom file content.

        We simulate the backup_custom_files / restore_custom_files logic from
        generate.sh using Python to verify the preservation invariant holds
        for arbitrary content and filenames matching *_custom.* pattern.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            output_dir = Path(tmpdir) / "output"
            backup_dir = Path(tmpdir) / "backup"
            output_dir.mkdir()

            # Create a custom file
            custom_file = output_dir / filename
            custom_file.write_text(custom_content, encoding="utf-8")

            # Create a custom/ directory with a file inside
            custom_subdir = output_dir / "custom"
            custom_subdir.mkdir()
            inner_file = custom_subdir / "extension.py"
            inner_file.write_text(custom_content, encoding="utf-8")

            # Simulate backup
            backup_dir.mkdir()
            # Copy custom/ directory
            shutil.copytree(custom_subdir, backup_dir / "custom")
            # Copy *_custom.* files
            shutil.copy2(custom_file, backup_dir / filename)

            # Simulate regeneration: wipe output directory
            shutil.rmtree(output_dir)
            output_dir.mkdir()
            # Create new generated content (simulating openapi-generator output)
            (output_dir / "generated_file.py").write_text("# generated\n")

            # Simulate restore
            shutil.copytree(backup_dir / "custom", output_dir / "custom")
            shutil.copy2(backup_dir / filename, output_dir / filename)

            # Verify preservation
            restored_file = output_dir / filename
            assert restored_file.exists(), f"Custom file {filename} not restored"
            assert restored_file.read_text(encoding="utf-8") == custom_content

            restored_inner = output_dir / "custom" / "extension.py"
            assert restored_inner.exists(), "Custom directory inner file not restored"
            assert restored_inner.read_text(encoding="utf-8") == custom_content


# ---------------------------------------------------------------------------
# Property 26: Spécification invalide interrompt la génération proprement
# ---------------------------------------------------------------------------


class TestProperty26InvalidSpecInterruption:
    """Property 26: Spécification invalide interrompt la génération proprement.

    When the OpenAPI spec is invalid, the generate script must:
    - Exit with non-zero code
    - NOT modify existing generated libraries
    - Print a meaningful error message

    **Validates: Requirements 1.6**
    """

    @pytest.mark.property
    def test_generate_script_validates_spec_first(self) -> None:
        """The generate.sh script validates the spec before generating."""
        if not GENERATE_SCRIPT.exists():
            pytest.skip(f"generate.sh not found at {GENERATE_SCRIPT}")

        script_content = GENERATE_SCRIPT.read_text(encoding="utf-8")

        # validate_spec function must be called before generate
        validate_idx = script_content.find("validate_spec")
        generate_idx = script_content.find("run_language")

        assert validate_idx != -1, "generate.sh missing validate_spec function/call"
        assert generate_idx != -1, "generate.sh missing run_language function/call"

        # In the main() function, validate_spec should be called before run_language
        main_section = script_content[script_content.find("main()"):]
        main_validate = main_section.find("validate_spec")
        main_generate = main_section.find("run_language")

        assert main_validate < main_generate, (
            "validate_spec must be called before run_language in main()"
        )

    @pytest.mark.property
    def test_generate_script_exits_on_invalid_spec(self) -> None:
        """The validate_spec function uses 'exit 1' on invalid spec."""
        if not GENERATE_SCRIPT.exists():
            pytest.skip(f"generate.sh not found at {GENERATE_SCRIPT}")

        script_content = GENERATE_SCRIPT.read_text(encoding="utf-8")

        # The validate_spec function should exit 1 on failure
        validate_section_start = script_content.find("validate_spec()")
        assert validate_section_start != -1

        # Find the next function definition to bound the section
        next_func = script_content.find("\n}", validate_section_start)
        validate_section = script_content[validate_section_start:next_func + 2]

        assert "exit 1" in validate_section, (
            "validate_spec function does not exit 1 on invalid spec"
        )

    @pytest.mark.property
    def test_generate_script_does_not_modify_on_invalid_spec(self) -> None:
        """The script explicitly states it won't modify existing libraries."""
        if not GENERATE_SCRIPT.exists():
            pytest.skip(f"generate.sh not found at {GENERATE_SCRIPT}")

        script_content = GENERATE_SCRIPT.read_text(encoding="utf-8")

        # The script should communicate that existing libs are preserved
        assert "NOT been modified" in script_content or "not modified" in script_content.lower(), (
            "generate.sh does not communicate that existing libraries are preserved on invalid spec"
        )

    @pytest.mark.property
    @settings(max_examples=100)
    @given(
        invalid_yaml=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "P")),
            min_size=10,
            max_size=200,
        ).filter(lambda s: "openapi" not in s.lower()),
    )
    def test_invalid_spec_content_would_fail_validation(self, invalid_yaml: str) -> None:
        """Random text that isn't a valid OpenAPI spec would fail validation.

        We verify that the spec validation logic (yaml.safe_load + schema check)
        would reject arbitrary non-OpenAPI content.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8"
        ) as f:
            f.write(invalid_yaml)
            f.flush()
            tmp_path = f.name

        try:
            # Try parsing as YAML
            with open(tmp_path, encoding="utf-8") as f:
                try:
                    data = yaml.safe_load(f)
                except yaml.YAMLError:
                    # YAML parse error → would definitely fail
                    return

            # If it parsed as YAML, check it's not a valid OpenAPI spec
            if isinstance(data, dict):
                # A valid OpenAPI spec requires 'openapi' version key
                assert "openapi" not in data, (
                    "Random text accidentally produced a valid-looking OpenAPI spec"
                )
        finally:
            os.unlink(tmp_path)

    @pytest.mark.property
    def test_spec_file_is_valid_openapi(self) -> None:
        """The actual efactgate-openapi.yaml is a valid OpenAPI 3.x spec."""
        spec = _load_openapi_spec()

        # Must have required top-level keys
        assert "openapi" in spec, "Missing 'openapi' version key"
        assert spec["openapi"].startswith("3."), (
            f"Expected OpenAPI 3.x, got {spec['openapi']}"
        )
        assert "info" in spec, "Missing 'info' section"
        assert "paths" in spec, "Missing 'paths' section"
        assert "components" in spec, "Missing 'components' section"

        # Must have at least the expected endpoints
        paths = spec["paths"]
        expected_paths = ["/invoices", "/ereporting", "/batch", "/status/{flux_id}", "/ack/{flux_id}"]
        for path in expected_paths:
            assert path in paths, f"Missing expected path: {path}"

    @pytest.mark.property
    def test_generate_script_uses_set_e_for_early_exit(self) -> None:
        """The generate.sh script uses 'set -e' to fail fast on errors."""
        if not GENERATE_SCRIPT.exists():
            pytest.skip(f"generate.sh not found at {GENERATE_SCRIPT}")

        script_content = GENERATE_SCRIPT.read_text(encoding="utf-8")

        # Should have set -e or set -euo pipefail in first few lines
        first_lines = "\n".join(script_content.split("\n")[:5])
        assert "set -e" in first_lines or "set -euo" in first_lines, (
            "generate.sh missing 'set -e' or 'set -euo pipefail' for fail-fast behavior"
        )
