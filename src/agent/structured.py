"""Schema definitions and a dependency-free JSON Schema subset validator."""

from __future__ import annotations

import json
import re
from typing import Any, Dict


class StructuredOutputError(ValueError):
    pass


def validate(value: Any, schema: Dict[str, Any], path: str = "$") -> None:
    """Validate the JSON Schema features used by the harness contracts."""
    expected = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
    }
    if expected and (expected not in types or not isinstance(value, types[expected])):
        raise StructuredOutputError("{} must be {}.".format(path, expected))
    if expected in {"integer", "number"} and isinstance(value, bool):
        raise StructuredOutputError("{} must be {}.".format(path, expected))
    if "enum" in schema and value not in schema["enum"]:
        raise StructuredOutputError(
            "{} must be one of {}.".format(path, schema["enum"])
        )
    if isinstance(value, str) and len(value) < schema.get("minLength", 0):
        raise StructuredOutputError(
            "{} is shorter than the allowed minimum.".format(path)
        )
    if isinstance(value, list):
        item_schema = schema.get("items", {})
        for index, item in enumerate(value):
            validate(item, item_schema, "{}[{}]".format(path, index))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise StructuredOutputError(
                "{} is missing {}.".format(path, ", ".join(missing))
            )
        if schema.get("additionalProperties") is False:
            unexpected = set(value) - set(properties)
            if unexpected:
                raise StructuredOutputError(
                    "{} has unexpected keys: {}.".format(
                        path, ", ".join(sorted(unexpected))
                    )
                )
        for key, item_schema in properties.items():
            if key in value:
                validate(value[key], item_schema, "{}.{}".format(path, key))


def parse_json(text: str) -> Dict[str, Any]:
    stripped = text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL | re.IGNORECASE
    )
    try:
        value = json.loads(fenced.group(1) if fenced else stripped)
    except json.JSONDecodeError as exc:
        raise StructuredOutputError("Model response was not valid JSON.") from exc
    if not isinstance(value, dict):
        raise StructuredOutputError("Model response must be a JSON object.")
    return value


STRING_LIST = {"type": "array", "items": {"type": "string"}}

INTAKE_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "accepted": {"type": "boolean"},
        "missing_or_invalid": STRING_LIST,
        "notes": STRING_LIST,
        "stage": {"type": "string", "enum": ["intake"]},
        "decision": {"type": "string", "enum": ["yes", "no"]},
    },
    "required": ["accepted", "missing_or_invalid", "notes", "stage", "decision"],
}

PLAN_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "summary": {"type": "string", "minLength": 20},
        "assumptions": STRING_LIST,
        "milestones": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "name": {"type": "string", "minLength": 1},
                    "objective": {"type": "string", "minLength": 1},
                    "files_or_areas": STRING_LIST,
                    "verification": STRING_LIST,
                },
                "required": ["name", "objective", "files_or_areas", "verification"],
            },
        },
        "risks": STRING_LIST,
        "architecture_review": {"type": "string"},
    },
    "required": [
        "summary",
        "assumptions",
        "milestones",
        "risks",
        "architecture_review",
    ],
}


def stage_schema(stage: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "stage": {"type": "string", "enum": [stage]},
            "decision": {"type": "string", "enum": ["yes", "no"]},
            "summary": {"type": "string"},
            "reason": {"type": "string"},
            "payload": payload,
        },
        "required": ["stage", "decision", "summary", "reason", "payload"],
    }


PLANNER_PLAN_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "plan": {
            "type": "object",
            "additionalProperties": False,
            "properties": PLAN_SCHEMA["properties"],
        }
    },
    "required": ["plan"],
}
PLANNER_RESULT_SCHEMA = stage_schema("planner", PLANNER_PLAN_PAYLOAD_SCHEMA)

CODER_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "changed_files": STRING_LIST,
        "checks_run": STRING_LIST,
        "acceptance_criteria_met": {"type": "boolean"},
        "patch": {"type": "string"},
    },
    "required": ["changed_files", "checks_run", "acceptance_criteria_met", "patch"],
}
CODER_RESULT_SCHEMA = stage_schema("coder", CODER_PAYLOAD_SCHEMA)

TESTER_PAYLOAD_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "test_cases_added": STRING_LIST,
        "commands_run": STRING_LIST,
        "all_checks_passed": {"type": "boolean"},
        "known_failures": STRING_LIST,
        "patch": {"type": "string"},
    },
    "required": [
        "test_cases_added",
        "commands_run",
        "all_checks_passed",
        "known_failures",
        "patch",
    ],
}
TESTER_RESULT_SCHEMA = stage_schema("tester", TESTER_PAYLOAD_SCHEMA)

CHAT_SUMMARY_RESULT_SCHEMA = stage_schema(
    "chat_summary",
    {
        "type": "object",
        "additionalProperties": False,
        "properties": {"summary": {"type": "string", "minLength": 20}},
        "required": ["summary"],
    },
)
