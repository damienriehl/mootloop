"""Attorney-gate decision service (plan P-28 / D11): generation from draft turns,
the append-only store, and resolution primitives.

Decisions live in ``runs/<run-id>/decisions/decisions.jsonl`` (append-only, fsync'd)
with a write-once ``decisions/<decision-id>.json`` sidecar carrying the original
proposal. The current view is the latest record per ``decision_id``. Generation is
idempotent: a logical gate (same kind + summary) is never proposed twice, even though
several draft/bolster turns per request each pass through here.
"""

from __future__ import annotations

import os
from pathlib import Path

from mootloop.errors import DecisionError
from mootloop.journal import append
from mootloop.models.decisions import (
    GATE_NAME_FOR_KIND,
    STATUS_FOR_ACTION,
    Decision,
    DecisionKind,
    DecisionOption,
    DecisionProposal,
    DecisionResolution,
    ResolutionAction,
    ResolutionSource,
    make_decision_id,
)
from mootloop.models.events import DecisionRecorded
from mootloop.models.matter import GateMode, MatterConfig
from mootloop.models.requests import RequestItem, code_from_request_id
from mootloop.models.run import DraftOutput, TurnSpec
from mootloop.vault import RunLock, atomic_write_text, safe_vault_path

DECISIONS_JSONL = "decisions.jsonl"


# --- store ------------------------------------------------------------------


class DecisionStore:
    """Append-only JSONL decision log, folded latest-per-``decision_id`` on read."""

    def __init__(self, vault_root: Path | str, run_id: str) -> None:
        self.vault_root = vault_root
        self.run_id = run_id
        self._path = safe_vault_path(vault_root, "runs", run_id, "decisions", DECISIONS_JSONL)

    def _records(self) -> list[Decision]:
        if not self._path.is_file():
            return []
        records: list[Decision] = []
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(Decision.model_validate_json(line))
        return records

    def folded(self) -> dict[str, Decision]:
        state: dict[str, Decision] = {}
        for record in self._records():
            state[record.decision_id] = record
        return state

    def list_all(self) -> list[Decision]:
        return list(self.folded().values())

    def list_open(self) -> list[Decision]:
        return [d for d in self.list_all() if d.status == "open"]

    def get(self, decision_id: str) -> Decision | None:
        return self.folded().get(decision_id)

    def append(self, decision: Decision) -> None:
        """Append one record; write the sidecar once (the immutable proposal copy)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(decision.model_dump_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        sidecar = safe_vault_path(
            self.vault_root, "runs", self.run_id, "decisions", f"{decision.decision_id}.json"
        )
        if not sidecar.exists():
            atomic_write_text(sidecar, decision.model_dump_json(indent=2) + "\n")


# --- gate taxonomy ----------------------------------------------------------


def gate_mode_for(matter: MatterConfig, kind: DecisionKind) -> GateMode:
    """The configured mode (``hard-human`` | ``policy-delegable``) for a decision kind,
    resolved from ``matter.yaml``'s gate list (plan D11 taxonomy)."""
    name = GATE_NAME_FOR_KIND[kind]
    for gate in matter.gates:
        if gate.name == name:
            return gate.mode
    # Fail safe: privilege / RFA default to human if the matter omits the gate.
    return "hard-human" if kind in _HARD_DEFAULT else "policy-delegable"


_HARD_DEFAULT = {DecisionKind.PRIVILEGE_CALL, DecisionKind.RFA_DISPOSITION}


def open_by_taxonomy(
    vault_root: Path | str, run_id: str, matter: MatterConfig, mode: GateMode
) -> list[Decision]:
    """Open decisions whose gate resolves to ``mode`` (hard-human or policy-delegable)."""
    return [
        d
        for d in DecisionStore(vault_root, run_id).list_open()
        if gate_mode_for(matter, d.kind) == mode
    ]


# --- generation -------------------------------------------------------------


def _opt(key: str, label: str, consequence: str) -> DecisionOption:
    return DecisionOption(key=key, label=label, consequence=consequence)


def _proposals_for_draft(
    spec: TurnSpec, draft: DraftOutput, code: str
) -> list[tuple[DecisionKind, str | None, DecisionProposal]]:
    """Every (kind, request_id, proposal) a single draft turn implies (plan P-28)."""
    request_id = str(spec.request_id) if spec.request_id else None
    out: list[tuple[DecisionKind, str | None, DecisionProposal]] = []

    # (c) One objection-posture call per request-type-set (not per request).
    out.append(
        (
            DecisionKind.OBJECTION_POSTURE,
            None,
            DecisionProposal(
                summary=f"Objection posture for {code.upper()} requests",
                reasoning=(
                    "Confirm the standing objection posture for this request type "
                    "before the responses are served."
                ),
                options=[
                    _opt("assert", "Assert objections", "Objections are preserved as drafted."),
                    _opt("narrow", "Narrow objections", "Keep only the strongest objections."),
                    _opt("waive", "Waive objections", "Answer fully without objecting."),
                ],
                recommended="assert" if draft.objections else "waive",
            ),
        )
    )

    # (a) Every attorney-gate item -> an unsupported-assertion call.
    for item in draft.attorney_gate_items:
        out.append(
            (
                DecisionKind.UNSUPPORTED_ASSERTION,
                request_id,
                DecisionProposal(
                    summary=f"Unsupported assertion in {request_id or spec.turn_id}: {item}",
                    reasoning=(
                        f"The associate could not ground this assertion: {item!r}. "
                        "Decide how to resolve it before service."
                    ),
                    options=[
                        _opt("obtain_support", "Obtain support", "Gather a grounding fact/source."),
                        _opt("strike", "Strike the assertion", "Remove it from the response."),
                        _opt("attest_anyway", "Attest anyway", "Keep it on attorney judgment."),
                    ],
                    recommended="obtain_support",
                ),
            )
        )

    # (d) Any privilege objection -> a privilege call.
    for objection in draft.objections:
        if objection.basis == "privilege":
            out.append(
                (
                    DecisionKind.PRIVILEGE_CALL,
                    request_id,
                    DecisionProposal(
                        summary=(
                            f"Privilege call in {request_id or spec.turn_id}: {objection.text}"
                        ),
                        reasoning=(
                            "A privilege objection was raised; every privilege call is a "
                            "human-by-design gate."
                        ),
                        options=[
                            _opt("withhold", "Withhold", "Assert privilege and log the item."),
                            _opt("log_only", "Log only", "Produce but note on the privilege log."),
                            _opt("produce", "Produce", "Waive privilege and produce."),
                        ],
                        recommended="withhold",
                    ),
                )
            )

    # (b) An RFA request -> the admit/deny/qualify/lack-of-knowledge disposition.
    if code == "rfa" and draft.rfa_disposition is not None:
        out.append(
            (
                DecisionKind.RFA_DISPOSITION,
                request_id,
                DecisionProposal(
                    summary=f"RFA disposition for {request_id or spec.turn_id}",
                    reasoning=(
                        "Every Rule 36 admit/deny/qualify/lack-of-knowledge is an attorney "
                        "gate; a non-conforming answer risks being deemed admitted."
                    ),
                    options=[
                        _opt("admit", "Admit", "Conclusively established."),
                        _opt("deny", "Deny", "Denial must fairly meet the substance."),
                        _opt("qualify", "Qualify", "Admit in part, deny in part."),
                        _opt(
                            "lack_of_knowledge",
                            "Lack of knowledge",
                            "Requires the reasonable-inquiry recital.",
                        ),
                    ],
                    recommended=draft.rfa_disposition,
                ),
            )
        )
    return out


# --- resolution (the decide primitive, plan D11) ----------------------------


def resolve(
    vault_root: Path | str,
    run_id: str,
    decision_id: str,
    action: ResolutionAction,
    chosen_key: str | None,
    note: str,
    decided_by: str,
    source: ResolutionSource,
    now: str,
) -> Decision:
    """Record a resolution for an open decision (append-only), emit a
    ``DecisionRecorded`` journal event, and re-finalize the run (plan Phase 5).

    Resolving an already-resolved decision is an error (no status regression). A
    ``modify`` requires a chosen key; an ``approve`` defaults to the recommendation.
    """
    with RunLock(vault_root, run_id):
        store = DecisionStore(vault_root, run_id)
        decision = store.get(decision_id)
        if decision is None:
            raise DecisionError(f"unknown decision {decision_id!r} in run {run_id!r}")
        if decision.status != "open":
            raise DecisionError(
                f"decision {decision_id!r} is already {decision.status}; decisions never regress"
            )
        if action == "modify" and not chosen_key:
            raise DecisionError("a 'modify' resolution requires --choose <key>")
        recommended = decision.proposal.recommended
        chosen = chosen_key if action != "approve" else (chosen_key or recommended)
        if chosen is not None and chosen not in {o.key for o in decision.proposal.options}:
            raise DecisionError(
                f"chosen key {chosen!r} is not an option for decision {decision_id!r}"
            )
        resolution = DecisionResolution(
            action=action,
            chosen_key=chosen,
            note=note,
            decided_by=decided_by,
            source=source,
            decided_at=now,
        )
        resolved = decision.model_copy(
            update={"status": STATUS_FOR_ACTION[action], "resolution": resolution}
        )
        store.append(resolved)
        append(
            vault_root,
            run_id,
            DecisionRecorded(
                decision_id=decision_id,
                decision_kind=decision.kind.value,
                action=action,
                status=resolved.status,
                decided_by=decided_by,
                source=source,
                decided_at=now,
            ),
        )
        # A resolved hard-human gate may let the run finish (plan Phase 5).
        from mootloop import orchestrator

        orchestrator.finalize_if_ready(vault_root, run_id, now)
    return resolved


def derive_and_store(
    vault_root: Path | str,
    run_id: str,
    spec: TurnSpec,
    draft: DraftOutput,
    units: list[RequestItem],
) -> list[Decision]:
    """Generate the attorney-gate decisions a recorded draft implies, skipping any that
    already exist (idempotent per logical gate). Returns the newly-stored decisions."""
    code = code_from_request_id(str(spec.request_id)) if spec.request_id else "all"
    store = DecisionStore(vault_root, run_id)
    existing = store.list_all()
    seen = {d.dedupe_key for d in existing}
    seq = len(existing)
    created: list[Decision] = []
    for kind, request_id, proposal in _proposals_for_draft(spec, draft, code):
        key = (kind.value, proposal.summary)
        if key in seen:
            continue
        seen.add(key)
        decision = Decision(
            decision_id=make_decision_id(run_id, seq),
            run_id=run_id,
            request_id=request_id,  # type: ignore[arg-type]
            kind=kind,
            proposal=proposal,
        )
        store.append(decision)
        created.append(decision)
        seq += 1
    return created
