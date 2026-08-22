"""Deterministic post-output evaluation for synthetic persona regressions."""

from __future__ import annotations

import json
from collections.abc import Iterable

from pydantic import ValidationError

from mootloop.errors import OracleError
from mootloop.models.oracles import (
    OracleCandidate,
    OracleEvaluation,
    OracleFailure,
    PersonaOracleAnswerKey,
    PersonaOracleCase,
)
from mootloop.models.run import OUTPUT_SCHEMAS, TurnSpec


def load_answer_key(raw: bytes) -> PersonaOracleAnswerKey:
    """Validate an explicitly supplied key; no product path is known or searched."""
    try:
        key = PersonaOracleAnswerKey.model_validate_json(raw)
    except ValidationError as exc:
        raise OracleError(f"synthetic oracle key is invalid: {exc}") from exc
    for case in key.cases:
        schema = OUTPUT_SCHEMAS.get(case.output_schema_name)
        if schema is None:
            raise OracleError(f"oracle case {case.case_id!r} names an unknown output schema")
        referenced = {*case.text_fields, *case.expected_values}
        missing = sorted(referenced - schema.model_fields.keys())
        if missing:
            raise OracleError(
                f"oracle case {case.case_id!r} references unknown output field(s): "
                + ", ".join(missing)
            )
    return key


def candidate_from_raw_turn(spec: TurnSpec, raw_text: str) -> OracleCandidate:
    """Parse one provider result through its planned output schema before evaluation."""
    schema = OUTPUT_SCHEMAS.get(spec.output_schema_name)
    if schema is None:
        raise OracleError(f"turn {spec.turn_id!s} names an unknown output schema")
    try:
        output = schema.model_validate_json(raw_text).model_dump(mode="json")
    except ValidationError as exc:
        raise OracleError(f"turn {spec.turn_id!s} output is schema-invalid: {exc}") from exc
    if spec.request_id is None:
        raise OracleError(f"turn {spec.turn_id!s} has no request identity")
    return OracleCandidate(
        persona=spec.persona,
        stage=spec.stage,
        request_id=str(spec.request_id),
        output_schema_name=spec.output_schema_name,
        output=output,
    )


def _candidate_identity(candidate: OracleCandidate) -> tuple[str, str, str, str]:
    return (
        candidate.persona.value,
        candidate.stage,
        candidate.request_id,
        candidate.output_schema_name,
    )


def _expected_identity(case: PersonaOracleCase) -> tuple[str, str, str, str]:
    return (case.persona.value, case.stage, case.request_id, case.output_schema_name)


def _field_text(value: object) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _evaluate_case(
    case: PersonaOracleCase, candidate: OracleCandidate
) -> list[OracleFailure]:
    failures: list[OracleFailure] = []
    schema = OUTPUT_SCHEMAS.get(case.output_schema_name)
    if schema is None:
        raise OracleError(f"oracle case {case.case_id!r} names an unknown output schema")
    try:
        validated = schema.model_validate(candidate.output).model_dump(mode="json")
    except ValidationError as exc:
        failures.append(
            OracleFailure(
                case_id=case.case_id,
                code="invalid_output_schema",
                detail=str(exc),
            )
        )
        return failures

    selected = "\n".join(_field_text(validated.get(field)) for field in case.text_fields)
    folded = selected.casefold()
    for alternatives in case.required_any:
        if not any(phrase.casefold() in folded for phrase in alternatives):
            failures.append(
                OracleFailure(
                    case_id=case.case_id,
                    code="missing_required_text",
                    detail="candidate omitted one required synthetic-answer concept",
                )
            )
    for phrase in case.forbidden:
        if phrase.casefold() in folded:
            failures.append(
                OracleFailure(
                    case_id=case.case_id,
                    code="forbidden_text",
                    detail=f"candidate included forbidden synthetic-answer text: {phrase!r}",
                )
            )
    for field, expected in case.expected_values.items():
        if validated.get(field) != expected:
            failures.append(
                OracleFailure(
                    case_id=case.case_id,
                    code="unexpected_value",
                    detail=f"candidate field {field!r} did not match the hidden expectation",
                )
            )
    return failures


def evaluate_answer_key(
    key: PersonaOracleAnswerKey, candidates: Iterable[OracleCandidate]
) -> OracleEvaluation:
    """Evaluate completed outputs only; key text is never returned or prompt-bound."""
    by_identity: dict[tuple[str, str, str, str], OracleCandidate] = {}
    by_request: dict[str, list[OracleCandidate]] = {}
    for candidate in candidates:
        identity = _candidate_identity(candidate)
        if identity in by_identity:
            raise ValueError(f"duplicate oracle candidate for {candidate.request_id}")
        by_identity[identity] = candidate
        by_request.setdefault(candidate.request_id, []).append(candidate)

    failures: list[OracleFailure] = []
    for case in key.cases:
        selected_candidate = by_identity.get(_expected_identity(case))
        if selected_candidate is None:
            same_request = by_request.get(case.request_id, [])
            if same_request:
                failures.append(
                    OracleFailure(
                        case_id=case.case_id,
                        code="candidate_identity_mismatch",
                        detail=(
                            "candidate persona, stage, request, or schema does not match the key"
                        ),
                    )
                )
                continue
            failures.append(
                OracleFailure(
                    case_id=case.case_id,
                    code="missing_candidate",
                    detail=f"no candidate was supplied for {case.request_id}",
                )
            )
            continue
        failures.extend(_evaluate_case(case, selected_candidate))
    return OracleEvaluation(
        key_id=key.key_id,
        passed=not failures,
        failures=tuple(failures),
    )
