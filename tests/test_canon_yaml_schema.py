import os

import pytest

jsonschema = pytest.importorskip("jsonschema")
yaml = pytest.importorskip("yaml")


@pytest.mark.skipif(
    not os.path.exists(
        os.path.join(
            os.path.dirname(__file__),
            "../extracted_from_usb/control_phases/myth/canon.schema.json",
        )
    ),
    reason="canon.schema.json not available",
)
def test_canon_yaml_schema():
    # Load canon.yaml and canon.schema.json
    base = os.path.dirname(__file__)
    canon_path = os.path.abspath(
        os.path.join(base, "../../egregore_control/myth/canon.yaml")
    )
    schema_path = os.path.abspath(
        os.path.join(
            base, "../extracted_from_usb/control_phases/myth/canon.schema.json"
        )
    )
    with open(canon_path, encoding="utf-8") as f:
        canon = yaml.safe_load(f)
    import json

    with open(schema_path, encoding="utf-8") as f:
        schema = json.load(f)
    # Validate
    validator = jsonschema.Draft7Validator(schema)
    errors = list(validator.iter_errors(canon))
    if errors:
        raise jsonschema.ValidationError(f"Validation failed: {errors[0].message}")
