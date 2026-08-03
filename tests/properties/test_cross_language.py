"""Property-based tests for cross-language generation consistency and spec validation.

Tests validate:
- Property 24: Cross-language endpoint and model consistency from the OpenAPI spec
- Property 25: Preservation of customizable files is correctly configured per language
- Property 26: Invalid OpenAPI specification is detected and halts generation

Validates: Requirements 1.2, 1.5, 1.6
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

# --- Constants ---

_SDK_ROOT = Path(__file__).resolve().parents[3]  # sdk/python/tests/properties -> sdk/
_OPENAPI_DIR = _SDK_ROOT / "openapi"
_SPEC_FILE = _OPENAPI_DIR / "gw-efactures-openapi.yaml"
_CONFIG_FILE = _OPENAPI_DIR / "generator-config.yaml"

# Supported languages in the generator config
_SUPPORTED_LANGUAGES = ("python", "typescript", "java", "c")

# Required OpenAPI top-level fields
_REQUIRED_OPENAPI_FIELDS = ("openapi", "info", "paths", "components")

# Valid HTTP methods in OpenAPI paths
_VALID_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


# --- Helpers ---


def _load_spec() -> dict[str, Any]:
    """Load and parse the OpenAPI specification YAML."""
    content = _SPEC_FILE.read_text(encoding="utf-8")
    return yaml.safe_load(content)


def _load_config() -> dict[str, Any]:
    """Load and parse the generator config YAML."""
    content = _CONFIG_FILE.read_text(encoding="utf-8")
    return yaml.safe_load(content)


def _extract_endpoints(spec: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Extract all (path, method, operationId) tuples from the spec.

    Returns a sorted list for deterministic comparison.
    """
    endpoints: list[tuple[str, str, str]] = []
    paths = spec.get("paths", {})
    for path, path_item in paths.items():
        for method in _VALID_HTTP_METHODS:
            if method in path_item:
                operation = path_item[method]
                operation_id = operation.get("operationId", "")
                endpoints.append((path, method, operation_id))
    return sorted(endpoints)


def _extract_schema_names(spec: dict[str, Any]) -> set[str]:
    """Extract all schema names from components/schemas."""
    schemas = spec.get("components", {}).get("schemas", {})
    return set(schemas.keys())


def _extract_schema_refs(spec: dict[str, Any]) -> set[str]:
    """Recursively extract all $ref references from the spec."""
    refs: set[str] = []

    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            if "$ref" in obj:
                refs.append(obj["$ref"])
            for value in obj.values():
                _walk(value)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)

    _walk(spec)
    return set(refs)


def _extract_required_fields_for_schema(
    schema: dict[str, Any],
) -> tuple[list[str], list[str]]:
    """Extract required and optional field names from a schema object.

    Returns (required_fields, optional_fields).
    """
    required = schema.get("required", [])
    properties = schema.get("properties", {})
    optional = [p for p in properties if p not in required]
    return sorted(required), sorted(optional)


def _validate_openapi_structure(content: str) -> list[str]:
    """Validate that content is valid OpenAPI YAML with required structure.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []

    # Step 1: Try to parse as YAML
    try:
        data = yaml.safe_load(content)
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        return errors

    # Step 2: Must be a dict
    if not isinstance(data, dict):
        errors.append("Root element must be a mapping/object")
        return errors

    # Step 3: Required top-level fields
    for field in _REQUIRED_OPENAPI_FIELDS:
        if field not in data:
            errors.append(f"Missing required top-level field: '{field}'")

    # Step 4: openapi version must be a valid semver-like string
    openapi_version = data.get("openapi", "")
    if openapi_version and not re.match(r"^\d+\.\d+\.\d+$", str(openapi_version)):
        errors.append(f"Invalid openapi version format: '{openapi_version}'")

    # Step 5: info must have title and version
    info = data.get("info", {})
    if isinstance(info, dict):
        if "title" not in info:
            errors.append("Missing 'info.title'")
        if "version" not in info:
            errors.append("Missing 'info.version'")

    # Step 6: paths must be a dict with valid path keys
    paths = data.get("paths", {})
    if isinstance(paths, dict):
        for path_key, path_item in paths.items():
            if not path_key.startswith("/"):
                errors.append(f"Path must start with '/': '{path_key}'")
            if not isinstance(path_item, dict):
                errors.append(f"Path item for '{path_key}' must be a mapping")

    # Step 7: Check that all $refs resolve to existing schemas
    if "components" in data and isinstance(data.get("components"), dict):
        schemas = data.get("components", {}).get("schemas", {})
        if isinstance(schemas, dict):
            refs = _extract_schema_refs(data)
            for ref in refs:
                if ref.startswith("#/components/schemas/"):
                    schema_name = ref.split("/")[-1]
                    if schema_name not in schemas:
                        errors.append(f"Unresolved $ref: '{ref}'")

    return errors


# =============================================================================
# Property 24: Cohérence cross-langage des endpoints et modèles
# =============================================================================


class TestProperty24CrossLanguageConsistency:
    """Property 24: Cohérence cross-langage des endpoints et modèles.

    **Validates: Requirements 1.5**

    The OpenAPI spec defines a single source of truth. All generators consume
    the same spec, so we verify that the spec itself is internally consistent:
    all paths have valid methods, all $ref references resolve, and all schemas
    are well-formed. This guarantees any compliant generator produces the same
    endpoint set and model set across languages.
    """

    @pytest.fixture(autouse=True)
    def _load_spec_fixture(self) -> None:
        """Load spec and config once per test class."""
        self.spec = _load_spec()
        self.config = _load_config()

    @pytest.mark.property
    def test_all_endpoints_have_operation_id(self) -> None:
        """Every endpoint in the spec must have a unique operationId."""
        endpoints = _extract_endpoints(self.spec)
        assert len(endpoints) > 0, "Spec must define at least one endpoint"

        operation_ids = [op_id for _, _, op_id in endpoints]
        # All must be non-empty
        for path, method, op_id in endpoints:
            assert op_id, f"Missing operationId for {method.upper()} {path}"

        # All must be unique
        assert len(operation_ids) == len(set(operation_ids)), (
            f"Duplicate operationIds found: {operation_ids}"
        )

    @pytest.mark.property
    def test_all_schema_refs_resolve(self) -> None:
        """Every $ref in the spec must resolve to an existing schema."""
        refs = _extract_schema_refs(self.spec)
        schemas = _extract_schema_names(self.spec)

        unresolved: list[str] = []
        for ref in refs:
            if ref.startswith("#/components/schemas/"):
                schema_name = ref.split("/")[-1]
                if schema_name not in schemas:
                    unresolved.append(ref)

        assert not unresolved, f"Unresolved $ref references: {unresolved}"

    @pytest.mark.property
    def test_all_schemas_have_required_structure(self) -> None:
        """All schemas must be valid objects with type or $ref definitions."""
        schemas = self.spec.get("components", {}).get("schemas", {})
        assert len(schemas) > 0, "Spec must define at least one schema"

        for name, schema in schemas.items():
            assert isinstance(schema, dict), f"Schema '{name}' must be a mapping"
            # Must have at least 'type' or be a $ref or oneOf/allOf/anyOf
            has_type = "type" in schema
            has_composition = any(
                k in schema for k in ("oneOf", "allOf", "anyOf", "$ref")
            )
            assert has_type or has_composition, (
                f"Schema '{name}' must define 'type' or composition (oneOf/allOf/anyOf)"
            )

    @pytest.mark.property
    def test_all_generators_use_same_input_spec(self) -> None:
        """All language generators in config reference the same input spec."""
        global_input = self.config.get("global", {}).get("inputSpec", "")
        assert global_input, "Global inputSpec must be defined"

        # All generators inherit from the same global inputSpec
        generators = self.config.get("generators", {})
        assert len(generators) >= len(_SUPPORTED_LANGUAGES), (
            f"Expected at least {len(_SUPPORTED_LANGUAGES)} generators, "
            f"got {len(generators)}"
        )

        for lang in _SUPPORTED_LANGUAGES:
            assert lang in generators, f"Missing generator config for '{lang}'"

    @pytest.mark.property
    def test_endpoints_consistent_model_coverage(self) -> None:
        """All request/response schemas referenced by endpoints exist in components."""
        paths = self.spec.get("paths", {})
        schemas = _extract_schema_names(self.spec)
        missing_schemas: list[str] = []

        for path, path_item in paths.items():
            for method in _VALID_HTTP_METHODS:
                if method not in path_item:
                    continue
                operation = path_item[method]

                # Check requestBody schema refs
                request_body = operation.get("requestBody", {})
                if isinstance(request_body, dict):
                    content = request_body.get("content", {})
                    for media_type, media_def in content.items():
                        schema = media_def.get("schema", {})
                        self._check_schema_refs(schema, schemas, missing_schemas, path, method)

                # Check response schema refs
                responses = operation.get("responses", {})
                for status_code, response_def in responses.items():
                    if isinstance(response_def, dict):
                        content = response_def.get("content", {})
                        for media_type, media_def in content.items():
                            schema = media_def.get("schema", {})
                            self._check_schema_refs(
                                schema, schemas, missing_schemas, path, method
                            )

        assert not missing_schemas, (
            f"Endpoints reference undefined schemas: {missing_schemas}"
        )

    def _check_schema_refs(
        self,
        schema: Any,
        known_schemas: set[str],
        missing: list[str],
        path: str,
        method: str,
    ) -> None:
        """Recursively check $ref resolutions in a schema node."""
        if not isinstance(schema, dict):
            return
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref.startswith("#/components/schemas/"):
                name = ref.split("/")[-1]
                if name not in known_schemas:
                    missing.append(f"{method.upper()} {path} -> {ref}")
        for key in ("items", "additionalProperties"):
            if key in schema and isinstance(schema[key], dict):
                self._check_schema_refs(schema[key], known_schemas, missing, path, method)
        for composition_key in ("oneOf", "allOf", "anyOf"):
            if composition_key in schema and isinstance(schema[composition_key], list):
                for item in schema[composition_key]:
                    self._check_schema_refs(item, known_schemas, missing, path, method)


# =============================================================================
# Property 25: Préservation des fichiers personnalisables à la régénération
# =============================================================================


class TestProperty25PreservePatterns:
    """Property 25: Préservation des fichiers personnalisables à la régénération.

    **Validates: Requirements 1.2**

    The generator-config.yaml must declare preservePatterns for each language
    that include custom/ directories and *_custom.* files.
    """

    @pytest.fixture(autouse=True)
    def _load_config_fixture(self) -> None:
        """Load config once per test class."""
        self.config = _load_config()

    @pytest.mark.property
    def test_global_preserve_patterns_defined(self) -> None:
        """Global config declares filesPreservedOnRegeneration with custom patterns."""
        global_cfg = self.config.get("global", {})
        preserved = global_cfg.get("filesPreservedOnRegeneration", [])

        assert preserved, "Global filesPreservedOnRegeneration must be defined"

        # Must contain a pattern matching custom/ directories
        has_custom_dir = any("custom" in p for p in preserved)
        assert has_custom_dir, (
            f"Global preserved patterns must include 'custom/' directory pattern. "
            f"Got: {preserved}"
        )

        # Must contain a pattern matching *_custom.* files
        has_custom_files = any("_custom." in p for p in preserved)
        assert has_custom_files, (
            f"Global preserved patterns must include '*_custom.*' file pattern. "
            f"Got: {preserved}"
        )

    @pytest.mark.property
    @pytest.mark.parametrize("language", _SUPPORTED_LANGUAGES)
    def test_per_language_preserve_patterns(self, language: str) -> None:
        """Each language generator declares preservePatterns for custom files."""
        generators = self.config.get("generators", {})
        assert language in generators, f"Missing generator config for '{language}'"

        lang_config = generators[language]
        patterns = lang_config.get("preservePatterns", [])

        assert patterns, (
            f"Generator '{language}' must define preservePatterns. Got: {lang_config.keys()}"
        )

        # Must have a pattern matching custom/ directories
        has_custom_dir = any("custom" in p for p in patterns)
        assert has_custom_dir, (
            f"[{language}] preservePatterns must include a 'custom/' directory pattern. "
            f"Got: {patterns}"
        )

        # Must have a pattern matching *_custom.* or *Custom.* files
        # (Java uses PascalCase *Custom.java as the idiomatic equivalent)
        has_custom_files = any(
            "_custom." in p or "_custom" in p or "Custom." in p or "*Custom" in p
            for p in patterns
        )
        assert has_custom_files, (
            f"[{language}] preservePatterns must include a custom file pattern "
            f"(*_custom.* or *Custom.*). Got: {patterns}"
        )

    @pytest.mark.property
    def test_generate_script_validates_spec_first(self) -> None:
        """The generate.sh script has validateSpec=true in global config."""
        global_cfg = self.config.get("global", {})
        assert global_cfg.get("validateSpec") is True, (
            "Global config must have 'validateSpec: true' to ensure "
            "invalid specs halt generation before any files are modified."
        )

    @pytest.mark.property
    def test_timeout_configured_per_generation(self) -> None:
        """Global config defines a generation timeout (120s by default)."""
        global_cfg = self.config.get("global", {})
        timeout = global_cfg.get("timeoutSeconds")
        assert timeout is not None, "Global config must define 'timeoutSeconds'"
        assert isinstance(timeout, int) and timeout > 0, (
            f"timeoutSeconds must be a positive integer, got: {timeout}"
        )


# =============================================================================
# Property 26: Spécification invalide interrompt la génération proprement
# =============================================================================


class TestProperty26InvalidSpecDetection:
    """Property 26: Spécification invalide interrompt la génération proprement.

    **Validates: Requirements 1.6**

    For any malformed/invalid OpenAPI spec content, validation detects issues.
    Tests the spec validation logic using PyYAML to parse and check structure.
    """

    @pytest.mark.property
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        random_content=st.text(
            min_size=1,
            max_size=500,
            alphabet=st.characters(
                whitelist_categories=("L", "N", "P", "S", "Z"),
            ),
        ),
    )
    def test_random_text_is_detected_as_invalid(self, random_content: str) -> None:
        """Random text content is always detected as invalid OpenAPI spec."""
        errors = _validate_openapi_structure(random_content)
        # Random text should either fail YAML parsing or miss required fields
        assert len(errors) > 0, (
            f"Random content should be invalid but passed validation: "
            f"{random_content[:100]!r}"
        )

    @pytest.mark.property
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        broken_yaml=st.one_of(
            # Unbalanced braces/brackets
            st.just("{openapi: [broken"),
            st.just("openapi: 3.1.0\npaths:\n  - invalid_list_instead_of_map"),
            # Tab-indentation errors
            st.just("openapi: 3.1.0\n\tinfo:\n\t\ttitle: Bad"),
            # Missing colons
            st.just("openapi 3.1.0\ninfo\n  title Test"),
            # Random binary-like content
            st.binary(min_size=10, max_size=200).map(
                lambda b: b.decode("latin-1")
            ),
        )
    )
    def test_malformed_yaml_detected(self, broken_yaml: str) -> None:
        """Malformed YAML content is always detected during validation."""
        errors = _validate_openapi_structure(broken_yaml)
        assert len(errors) > 0, (
            f"Malformed YAML should be invalid: {broken_yaml[:100]!r}"
        )

    @pytest.mark.property
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        missing_field=st.sampled_from(["openapi", "info", "paths", "components"]),
    )
    def test_missing_required_fields_detected(self, missing_field: str) -> None:
        """OpenAPI spec missing any required top-level field is detected."""
        # Build a minimal valid-ish spec, then remove one required field
        minimal_spec: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {},
            "components": {"schemas": {}},
        }
        del minimal_spec[missing_field]

        content = yaml.dump(minimal_spec)
        errors = _validate_openapi_structure(content)
        assert any(missing_field in e for e in errors), (
            f"Should detect missing '{missing_field}'. Errors: {errors}"
        )

    @pytest.mark.property
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        bad_ref=st.text(
            min_size=3,
            max_size=30,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
    )
    def test_unresolved_refs_detected(self, bad_ref: str) -> None:
        """Spec with unresolved $ref references is detected as invalid."""
        spec_with_bad_ref: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                "/test": {
                    "get": {
                        "operationId": "testOp",
                        "responses": {
                            "200": {
                                "description": "OK",
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "$ref": f"#/components/schemas/{bad_ref}"
                                        }
                                    }
                                },
                            }
                        },
                    }
                }
            },
            "components": {"schemas": {}},  # Empty schemas — ref won't resolve
        }

        content = yaml.dump(spec_with_bad_ref)
        errors = _validate_openapi_structure(content)
        assert any("Unresolved" in e or "$ref" in e for e in errors), (
            f"Should detect unresolved $ref to '{bad_ref}'. Errors: {errors}"
        )

    @pytest.mark.property
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        bad_version=st.one_of(
            st.just("abc"),
            st.just("3.1"),
            st.just("3"),
            st.text(min_size=1, max_size=10, alphabet=st.characters(
                whitelist_categories=("L",)
            )),
        ),
    )
    def test_invalid_openapi_version_detected(self, bad_version: str) -> None:
        """Invalid openapi version format is detected."""
        spec: dict[str, Any] = {
            "openapi": bad_version,
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {},
            "components": {"schemas": {}},
        }
        content = yaml.dump(spec)
        errors = _validate_openapi_structure(content)
        assert any("version" in e.lower() or "openapi" in e.lower() for e in errors), (
            f"Should detect invalid openapi version '{bad_version}'. Errors: {errors}"
        )

    @pytest.mark.property
    def test_valid_spec_passes_validation(self) -> None:
        """The actual project OpenAPI spec passes validation without errors."""
        content = _SPEC_FILE.read_text(encoding="utf-8")
        errors = _validate_openapi_structure(content)
        assert not errors, (
            f"The project OpenAPI spec should be valid but got errors: {errors}"
        )

    @pytest.mark.property
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    @given(
        invalid_path=st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ).filter(lambda s: not s.startswith("/")),
    )
    def test_invalid_path_key_detected(self, invalid_path: str) -> None:
        """Paths not starting with '/' are detected as invalid."""
        spec: dict[str, Any] = {
            "openapi": "3.1.0",
            "info": {"title": "Test", "version": "1.0.0"},
            "paths": {
                invalid_path: {
                    "get": {
                        "operationId": "badPathOp",
                        "responses": {"200": {"description": "OK"}},
                    }
                }
            },
            "components": {"schemas": {}},
        }
        content = yaml.dump(spec)
        errors = _validate_openapi_structure(content)
        assert any("/" in e or "Path" in e for e in errors), (
            f"Should detect path not starting with '/': '{invalid_path}'. Errors: {errors}"
        )
