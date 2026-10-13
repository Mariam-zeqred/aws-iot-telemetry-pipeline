"""The Glue/Parquet column list must cover every field the ingest Lambda writes."""

from common.telemetry import OPTIONAL_FIELDS, REQUIRED_FIELDS, enrich, validate
from pipeline.storage import PARQUET_COLUMNS
from tests.test_telemetry import GOOD, NOW


def test_every_enriched_field_has_a_parquet_column() -> None:
    full = enrich(validate(GOOD, now=NOW), device_id="x", received_at=NOW, ttl_days=30)
    columns = {name for name, _ in PARQUET_COLUMNS}
    missing = {k.lower() for k in full} - columns
    assert not missing, missing
    assert {k.lower() for k in REQUIRED_FIELDS | OPTIONAL_FIELDS} <= columns


def test_parquet_types_match_python_types() -> None:
    kinds = dict(PARQUET_COLUMNS)
    for name, py in (REQUIRED_FIELDS | OPTIONAL_FIELDS).items():
        glue = kinds[name.lower()]
        expected = {bool: "boolean", int: {"int", "bigint"}, float: "double", str: "string"}[py]
        assert glue in expected if isinstance(expected, set) else glue == expected, name
