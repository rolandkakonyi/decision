"""Common request envelope and bundled providers' schema restrictions."""


def request_schema():
    from decision_cli.images import assets_schema

    variants = []
    for kind in ("noul", "choice", "score", "box"):
        fields = {"type": {"const": kind}, "instructions": {"type": "string", "minLength": 1}}
        required = ["type", "instructions"]
        if kind == "choice":
            fields["criteria"] = {"type": "object", "minProperties": 1, "propertyNames": {"minLength": 1}}
            required.append("criteria")
        elif kind == "score":
            fields["criteria"] = {"type": "array", "minItems": 1}
            required.append("criteria")
        elif kind == "noul":
            fields["criteria"] = {"type": ["object", "null"], "properties": {"true": {}, "false": {}}, "additionalProperties": False}
        else:
            fields["criteria"] = {}
        variants.append({"type": "object", "properties": fields, "required": required, "additionalProperties": False})
    return {
        "type": "object", "required": ["state", "questions"], "additionalProperties": False,
        "properties": {
            "state": {"description": "Shared context for every independent question. JSON value, excluding null.",
                      "type": ["object", "array", "string", "number", "boolean"]},
            "questions": {"type": "object", "minProperties": 1, "propertyNames": {"minLength": 1},
                          "description": "Named questions evaluated together in ONE provider request.",
                          "additionalProperties": {"oneOf": variants}},
            "model": {"type": "string", "minLength": 1, "description": "Optional model override; defaults to server configuration."},
            "assets": assets_schema(),
        },
    }


def decision_constraints(max_questions, max_score=10, id_pattern=None, option_length=None):
    """Shared schema shape for the bundled Noul/Choice/Score wire APIs."""
    questions = {"maxProperties": max_questions}
    if id_pattern:
        questions["propertyNames"] = {"pattern": id_pattern}
    variants = []
    for kind in ("noul", "choice", "score"):
        fields = {"type": {"const": kind}}
        if kind == "choice":
            fields["criteria"] = {"minProperties": 2, "maxProperties": 255}
            if option_length:
                fields["criteria"]["propertyNames"] = {"maxLength": option_length}
        elif kind == "score":
            fields["criteria"] = {"minItems": 2, "maxItems": max_score}
        variants.append({"properties": fields, "required": ["type"]})
    questions["additionalProperties"] = {"oneOf": variants}
    return {"properties": {"questions": questions}}
