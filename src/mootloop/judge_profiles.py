"""Assigned-judge public-opinion retrieval, calibration, and immutable profile storage."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from mootloop.citations import http
from mootloop.citations.courtlistener import CL_HOST, TOKEN_ENV
from mootloop.citations.courtlistener_opinions import (
    OpinionAuthorityStore,
    fetch_case_authority,
)
from mootloop.citations.ledger import ResearchQueue
from mootloop.errors import CitationError
from mootloop.models.citations import (
    OpinionAuthorityStoreRecord,
    ResearchRequest,
    make_citation_id,
)
from mootloop.models.common import MatterId
from mootloop.models.context import ContextContribution
from mootloop.models.judge_profiles import (
    JudgeCalibration,
    JudgeOpinionRef,
    JudgeProfile,
)
from mootloop.models.matter import MatterConfig
from mootloop.models.run import PersonaName
from mootloop.vault import atomic_write_once_text, atomic_write_text, safe_vault_path

SEARCH_PATH = "/api/rest/v4/search/"
PROFILE_DIR = ("law", "judge-profiles")
MAX_PROFILE_OPINIONS = 20
MIN_TRAINING_EXAMPLES = 4
MIN_HOLDOUT_EXAMPLES = 2
MAX_CALIBRATED_ERROR = 0.35

_SAFE_JUDGE_NAME = re.compile(r"^[A-Za-z][A-Za-z .,'-]{0,119}$")
_PROFILE_ID = re.compile(r"^judge-profile-[0-9a-f]{16}$")
_JUDICIAL_TITLE = re.compile(r"^(?:the\s+honorable|honorable|hon\.?|judge|justice)\s+", re.I)
_DISPOSITIONS = (
    re.compile(r"motion to compel.{0,160}?\b(granted|denied)\b", re.IGNORECASE | re.DOTALL),
    re.compile(r"\b(grants?|denies?|denied)\b.{0,160}?motion to compel", re.IGNORECASE | re.DOTALL),
)
_US_JURISDICTIONS = frozenset(
    {
        "AK", "AL", "AR", "AS", "AZ", "CA", "CO", "CT", "DC", "DE", "FL",
        "GA", "GU", "HI", "IA", "ID", "IL", "IN", "KS", "KY", "LA", "MA",
        "MD", "ME", "MI", "MN", "MO", "MP", "MS", "MT", "NC", "ND", "NE",
        "NH", "NJ", "NM", "NV", "NY", "OH", "OK", "OR", "PA", "PR", "RI",
        "SC", "SD", "TN", "TX", "UT", "VA", "VI", "VT", "WA", "WI", "WV",
        "WY",
    }
)


@dataclass(frozen=True)
class JudgeProfileBuildResult:
    profile: JudgeProfile | None
    warning: str = ""
    research_request_id: str | None = None


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalized_name(name: str) -> str:
    return " ".join(name.split())


def _judge_key(name: str) -> str:
    normalized = _normalized_name(name)
    while (stripped := _JUDICIAL_TITLE.sub("", normalized, count=1)) != normalized:
        normalized = stripped
    return normalized.casefold()


def _query(name: str) -> str:
    return f'judge:"{name}" AND "motion to compel"'


def _explicit_disposition(text: str) -> Literal["granted", "denied"] | None:
    labels: set[Literal["granted", "denied"]] = set()
    for pattern in _DISPOSITIONS:
        for match in pattern.finditer(text):
            raw = match.group(1).casefold()
            labels.add("denied" if raw.startswith("den") else "granted")
    return next(iter(labels)) if len(labels) == 1 else None


def _profile_identity(
    judge_name: str,
    jurisdiction_state: str,
    court_name: str,
    opinions: list[JudgeOpinionRef],
) -> str:
    sources = "\n".join(
        f"{ref.cluster_id}:{ref.content_sha256}:{ref.disposition}:{ref.calibration_split}"
        for ref in opinions
    )
    digest = _sha(f"{judge_name}\n{jurisdiction_state}\n{court_name}\n{sources}")[:16]
    return f"judge-profile-{digest}"


def calibrate_judge_profile(
    *,
    judge_name: str,
    jurisdiction_state: str,
    court_name: str,
    snapshots: list[OpinionAuthorityStoreRecord],
    built_at: str,
) -> JudgeProfile:
    """Distill explicit dispositions and measure a majority baseline on held-out data."""
    name = _normalized_name(judge_name)
    query_sha = _sha(_query(name))
    labeled = [
        (snapshot, disposition)
        for snapshot in sorted(snapshots, key=lambda item: item.cluster_id)
        if (disposition := _explicit_disposition(snapshot.text)) is not None
    ][:MAX_PROFILE_OPINIONS]
    refs: list[JudgeOpinionRef] = []
    training_labels: list[str] = []
    holdout_labels: list[str] = []
    for index, (snapshot, disposition) in enumerate(labeled, start=1):
        split: Literal["training", "holdout"] = (
            "holdout" if index % 5 == 0 else "training"
        )
        if split == "training":
            training_labels.append(disposition)
        else:
            holdout_labels.append(disposition)
        refs.append(
            JudgeOpinionRef(
                citation_id=snapshot.citation_id,
                cluster_id=snapshot.cluster_id,
                source_url=snapshot.source_url,
                content_sha256=snapshot.content_sha256,
                disposition=disposition,
                calibration_split=split,
            )
        )
    baseline: Literal["granted", "denied"] | None = None
    if training_labels:
        granted = training_labels.count("granted")
        denied = training_labels.count("denied")
        baseline = "granted" if granted >= denied else "denied"
    correct = sum(label == baseline for label in holdout_labels) if baseline else 0
    error = 1.0 - (correct / len(holdout_labels)) if holdout_labels and baseline else None
    calibrated = (
        len(training_labels) >= MIN_TRAINING_EXAMPLES
        and len(holdout_labels) >= MIN_HOLDOUT_EXAMPLES
        and error is not None
        and error <= MAX_CALIBRATED_ERROR
    )
    calibration = JudgeCalibration(
        training_examples=len(training_labels),
        holdout_examples=len(holdout_labels),
        baseline_disposition=baseline,
        correct_holdout_predictions=correct,
        error_rate=error,
        maximum_calibrated_error=MAX_CALIBRATED_ERROR,
        calibrated=calibrated,
        limits=[
            "Public CourtListener opinions only.",
            "Only explicit motion-to-compel dispositions were labeled.",
            "Historical dispositions do not predict an outcome in a new matter.",
        ],
    )
    status = "calibrated" if calibrated else "not calibrated"
    error_text = f"{error:.0%}" if error is not None else "not measurable"
    prompt_text = (
        f"Directional assigned-judge profile for {name}. Status: {status}. "
        f"Training examples: {len(training_labels)}; held-out examples: "
        f"{len(holdout_labels)}; held-out error: {error_text}. "
        f"Observed majority explicit motion-to-compel disposition: "
        f"{baseline or 'insufficient evidence'}. Treat this as a bounded historical "
        "signal only, never as a legal conclusion or outcome prediction. "
        "Sources are exact public-opinion hashes recorded with this profile."
    )
    profile_id = _profile_identity(name, jurisdiction_state, court_name, refs)
    return JudgeProfile(
        profile_id=profile_id,
        judge_name=name,
        jurisdiction_state=jurisdiction_state,
        court_name=court_name,
        built_at=built_at,
        source_query_sha256=query_sha,
        opinions=refs,
        calibration=calibration,
        prompt_text=prompt_text,
        directional_only=True,
    )


def _research_request(
    vault_root: Path | str,
    judge_name: str,
    reason: str,
) -> str:
    normalized = f"assigned-judge profile: {_normalized_name(judge_name)}"
    citation_id = make_citation_id(normalized)
    request_id = f"research-judge-{_sha(normalized)[:16]}"
    queue = ResearchQueue(vault_root)
    if queue.get(request_id) is None:
        queue.append(
            ResearchRequest(
                request_id=request_id,
                citation_id=citation_id,
                normalized=normalized,
                reason=reason,
            )
        )
    return request_id


def _result_matches_judge(result: dict[str, object], judge_name: str) -> bool:
    candidates: list[str] = []
    judge = result.get("judge")
    if isinstance(judge, str):
        candidates.append(judge)
    panel = result.get("panel_names")
    if isinstance(panel, list):
        candidates.extend(value for value in panel if isinstance(value, str))
    target = _judge_key(judge_name)
    return any(_judge_key(candidate) == target for candidate in candidates)


def fetch_judge_opinions(
    judge_name: str,
    fetched_at: str,
    *,
    transport: http.Transport | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> tuple[list[OpinionAuthorityStoreRecord], str]:
    """Fetch at most twenty exact public opinions from a fixed CourtListener search."""
    name = _normalized_name(judge_name)
    if not _SAFE_JUDGE_NAME.fullmatch(name):
        return [], "assigned judge name is not safe for the fixed public search"
    renew = heartbeat or (lambda: None)
    renew()
    try:
        response = http.fetch(
            http.HttpRequest(
                "GET",
                CL_HOST,
                SEARCH_PATH,
                params={"type": "o", "q": _query(name), "order_by": "dateFiled desc"},
                auth_token_env=TOKEN_ENV,
            ),
            transport=transport,
        )
    except http.HttpError as exc:
        return [], f"CourtListener judge search error: {type(exc).__name__}"
    renew()
    if response.status_code != 200 or not isinstance(response.json_body, dict):
        return [], f"CourtListener judge search returned {response.status_code}"
    raw_results = response.json_body.get("results")
    if not isinstance(raw_results, list):
        return [], "CourtListener judge search returned no result list"
    snapshots: list[OpinionAuthorityStoreRecord] = []
    seen: set[int] = set()
    for raw in raw_results:
        if not isinstance(raw, dict) or not _result_matches_judge(raw, name):
            continue
        cluster_id = raw.get("cluster_id")
        if not isinstance(cluster_id, int) or cluster_id < 1 or cluster_id in seen:
            continue
        seen.add(cluster_id)
        absolute_url = raw.get("absolute_url")
        source_url = (
            f"https://{CL_HOST}{absolute_url}"
            if isinstance(absolute_url, str) and absolute_url.startswith("/opinion/")
            else f"https://{CL_HOST}/opinion/{cluster_id}/search-result/"
        )
        renew()
        fetched = fetch_case_authority(
            citation_id=make_citation_id(f"judge-profile:{name}:{cluster_id}"),
            source_url=source_url,
            fetched_at=fetched_at,
            transport=transport,
            heartbeat=heartbeat,
        )
        renew()
        if fetched.snapshot is not None:
            snapshots.append(fetched.snapshot)
        if len(snapshots) == MAX_PROFILE_OPINIONS:
            break
    return snapshots, "" if snapshots else "no exact public opinions were available"


class JudgeProfileStore:
    """Content-addressed profile archive plus one verified current-profile pointer."""

    def __init__(self, vault_root: Path | str) -> None:
        self.vault_root = vault_root

    def _archive_path(self, profile_id: str) -> Path:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise CitationError("judge profile id is invalid")
        return safe_vault_path(self.vault_root, *PROFILE_DIR, f"{profile_id}.json")

    def publish(self, profile: JudgeProfile) -> Path:
        path = self._archive_path(profile.profile_id)
        body = profile.model_dump_json(indent=2) + "\n"
        try:
            atomic_write_once_text(path, body)
        except FileExistsError:
            existing_body = path.read_text(encoding="utf-8")
            existing = JudgeProfile.model_validate_json(existing_body)
            same_evidence = existing.model_copy(update={"built_at": profile.built_at})
            if same_evidence != profile:
                raise CitationError("content-addressed judge profile conflicts") from None
            body = existing_body
        atomic_write_text(
            safe_vault_path(self.vault_root, *PROFILE_DIR, "current.json"), body
        )
        return path

    def latest(self) -> JudgeProfile:
        path = safe_vault_path(self.vault_root, *PROFILE_DIR, "current.json")
        if not path.is_file():
            raise CitationError("no assigned-judge profile has been built")
        profile = JudgeProfile.model_validate_json(path.read_text(encoding="utf-8"))
        expected = _profile_identity(
            profile.judge_name,
            profile.jurisdiction_state,
            profile.court_name,
            profile.opinions,
        )
        if profile.profile_id != expected:
            raise CitationError("judge profile identity does not match its sources")
        archive_path = self._archive_path(profile.profile_id)
        if not archive_path.is_file():
            raise CitationError("current judge profile has no write-once archive")
        archived = JudgeProfile.model_validate_json(
            archive_path.read_text(encoding="utf-8")
        )
        if archived != profile:
            raise CitationError("current judge profile does not match its archive")
        return profile

    def latest_or_none(self) -> JudgeProfile | None:
        path = safe_vault_path(self.vault_root, *PROFILE_DIR, "current.json")
        return self.latest() if path.is_file() else None


def profile_context_contribution(
    profile: JudgeProfile,
    matter_id: MatterId | str,
) -> ContextContribution:
    """Convert only an empirically calibrated profile into next-run judge DATA."""
    if not profile.calibration.calibrated:
        raise CitationError("uncalibrated judge profile cannot enter a run context")
    text = profile.prompt_text
    return ContextContribution(
        contribution_id=profile.profile_id,
        kind="context_note",
        text=text,
        sha256=_sha(text),
        provenance_locator=f"law/judge-profiles/{profile.profile_id}.json",
        source_matter_id=MatterId(str(matter_id)),
        persona_scope=(PersonaName.JUDGE,),
        trust="untrusted_data",
        permission="matter_confidential",
        approval_state="approved",
    )


def profile_matches_matter(profile: JudgeProfile, matter: MatterConfig) -> bool:
    """Match a stored profile using the same canonical identity used at build time."""
    judge_name = matter.caption.judge_name
    return bool(
        judge_name
        and profile.judge_name == _normalized_name(judge_name)
        and profile.court_name == matter.caption.court_name
        and profile.jurisdiction_state == matter.jurisdiction.state.strip()
    )


def build_assigned_judge_profile(
    vault_root: Path | str,
    matter: MatterConfig,
    built_at: str,
    *,
    transport: http.Transport | None = None,
    heartbeat: Callable[[], None] | None = None,
) -> JudgeProfileBuildResult:
    """Build and store an honest profile, or emit an explicit human-research item."""
    judge_name = matter.caption.judge_name
    if not judge_name:
        reason = "matter caption has no assigned judge"
        return JudgeProfileBuildResult(
            None,
            reason,
            _research_request(vault_root, "unknown assigned judge", reason),
        )
    state = matter.jurisdiction.state.strip()
    if state.upper() not in _US_JURISDICTIONS:
        warning = f"non-US jurisdiction {state!r} is not supported by CourtListener profiling"
        return JudgeProfileBuildResult(
            None,
            warning,
            _research_request(vault_root, judge_name, warning),
        )
    snapshots, warning = fetch_judge_opinions(
        judge_name,
        built_at,
        transport=transport,
        heartbeat=heartbeat,
    )
    if not snapshots:
        reason = warning or "no public assigned-judge opinions were available"
        return JudgeProfileBuildResult(
            None,
            reason,
            _research_request(vault_root, judge_name, reason),
        )
    authority_store = OpinionAuthorityStore(vault_root)
    for snapshot in snapshots:
        if heartbeat is not None:
            heartbeat()
        authority_store.capture(snapshot)
    profile = calibrate_judge_profile(
        judge_name=judge_name,
        jurisdiction_state=state,
        court_name=matter.caption.court_name,
        snapshots=snapshots,
        built_at=built_at,
    )
    if heartbeat is not None:
        heartbeat()
    JudgeProfileStore(vault_root).publish(profile)
    if profile.calibration.calibrated:
        return JudgeProfileBuildResult(profile)
    reason = "public-opinion profile did not meet the held-out calibration threshold"
    return JudgeProfileBuildResult(
        profile,
        reason,
        _research_request(vault_root, judge_name, reason),
    )
