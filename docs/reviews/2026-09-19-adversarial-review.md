# MootLoop adversarial production-code review — 2026-09-19

## Triage summary

| ID | Severity | Defect | Concrete consequence |
| --- | --- | --- | --- |
| R01 | High | Turn completion precedes required follow-up records | Missing human gates, lost spend, or stranded completed runs |
| R02 | High | Status readers truncate journals without writer coordination | A read deletes newly committed events |
| R03 | High | Restore bypasses vault-location preflight | Matter contents can be restored into a repository or sync folder |
| R04 | High | Restore deletes a target created after its initial check | Concurrent vault destroyed despite `overwrite=False` |
| R05 | High | Close purges before recording durable closure evidence | Deleted matter has no recoverable close transaction |
| R06 | High | First-use key creation races | Backups encrypted with the losing key cannot be restored using the selected key |
| R07 | High | Canary registration is an unlocked multi-file update | Live vault canaries disappear from the outbound registry |
| R08 | High | Restore does not register restored canaries | Cross-host restore loses its canary tripwire |
| R09 | High | Request identity omits served-set identity | One set's response is exported for another set's request |
| R10 | High | Non-RFA decisions do not change exported responses | Waived objections and struck assertions remain in output |
| R11 | High | RFA renderer discards the response narrative | Qualified admissions lose their actual qualification |
| R12 | Medium | RFP withholding statement is inferred from objections | Export contradicts a response saying nothing is withheld |
| R13 | Medium | Learning acceptance commits before its contribution | Accepted learning cannot be published by retrying acceptance |
| R14 | Medium | Journal terminal-record handling loses boundaries | Complete corrupt records are erased; valid unterminated records concatenate |
| R15 | Medium | Decision log cannot recover an interrupted append | All decision reads and resolution retries fail |
| R16 | Medium | SSE reconnect replays into an additive reducer | Displayed spend and completed-turn counts inflate |
| R17 | Medium | Worker ID is used as an unchecked path | CLI input writes heartbeat JSON outside the queue directory |
| R18 | Medium | Matter enumeration and resolution disagree on identity | Selecting a listed matter opens another directory or fails |
| R19 | Medium | Local privacy registry accepts invalid shapes as empty | A malformed policy silently disables registered tripwires |

## Scope and evidence

Reviewed source snapshot: `597180cb4241de2f19f37a992924b9a366db0773`, on local branch `review/adversarial-20260919`. Its base is `da339fffbd7828819184c6655c27d678a9809a2a`; the snapshot additionally contains the unmerged U-13 board contracts/persistence and associated vault/close changes. U-12 and U-16 branches were not included. The findings below concern pre-existing production paths; none requires activating the U-13 scaffold.

This was an offline, source-only review. Scenarios below are deductions from the cited implementation, **not claims that incidents occurred or that reproductions were executed**. Confidence describes the code-level causal chain. No tests, application commands, deployment, migration, or network operations were run. No actual vault, credential file, `.env`, private key, or prohibited secret directory was read. The only authored repository artifact is this report; unrelated workspace changes are outside its scope. Test/lint hooks are intentionally not executed for the documentation commit under the read-only task restriction.

High means confidentiality, destructive loss, substantive output error, or loss of required review/accounting controls. Medium means a bounded integrity, availability, or operator-input failure. Conditions such as concurrent first use, an interrupted write, or a malformed restored archive are stated explicitly rather than assumed to exist in a deployment.

## Findings

### R01 — A durable `TurnCompleted` can permanently bypass required decision, spend, rubric, and finalization work.

**Locations:** `src/mootloop/orchestrator.py:643-659`, `src/mootloop/orchestrator.py:459-475`, `src/mootloop/orchestrator.py:1093-1100`, `src/mootloop/engine/worker.py:362-366`, `src/mootloop/journal.py:308-325`, `src/mootloop/gate_ledger.py:135-159`; the same accounting ordering occurs at `src/mootloop/citations/check_runner.py:192-211` and `src/mootloop/citations/check_runner.py:260-272`.

**Failure scenario:** The final operative draft introduces a privilege objection not present in earlier drafts. Its completion is appended and fsynced, then the process dies before `derive_and_store`. Recovery skips the completed slot. Finalization and the gate ledger consult existing decisions, so the absent privilege decision is not a blocker. An attorney can subsequently attest the document without ever receiving that dedicated decision. The general attestation requirement still applies; the lost control is the specific human decision.

The same commit gap has two additional concrete outcomes. A crash before `_book_spend` loses actual usage while folding `TurnCompleted` removes its conservative reservation; ordinary worker recovery has no saved result usage to settle. Citation-check recovery explicitly passes `usage=None`. A crash after the last rubric seat but before aggregation/finalization leaves no schedulable turns: the worker completes the queue item, leaving the run `running` with missing rubric evidence. The direct provider driver can finalize but does not reconstruct the rubric event.

**Confidence:** High. These are adjacent durable writes and explicit recovery branches. **Repair direction:** Persist settlement inputs and make completed-turn effects idempotently recoverable before finalization or queue completion; retain spend reservations until settlement.

### R02 — An unlocked journal reader can truncate events committed after its snapshot.

**Locations:** `src/mootloop/journal.py:193-228`, `src/mootloop/journal.py:280-286`, `src/mootloop/web/api/routes.py:148-156`, `src/mootloop/web/api/sse.py:68-76`.

**Failure scenario:** A prior crash leaves complete prefix P followed by a torn record. A status request snapshots that file and pauses before `_truncate`. A recovering worker obtains the normal run lock, reads and repairs the same tail, then appends and fsyncs a new completion or spend event. The status reader resumes and unconditionally truncates the live file to the old end of P, deleting the worker's durable event. This interleaving does not require the operating system to split a new append. API list/status and SSE polling do not take the writer's lock.

**Confidence:** High. **Repair direction:** Keep readers nonmutating; repair under the writer's serialization boundary after rereading the current tail.

### R03 — Restore writes matter contents without enforcing repository and sync-folder exclusion.

**Locations:** `src/mootloop/cli/operations.py:260-273`, `src/mootloop/engine/backup.py:314-334`; compare `src/mootloop/vault.py:318-354`.

**Failure scenario:** An operator supplies a valid backup and a `--matters-root` beneath a Git worktree or a recognized background-sync folder. The CLI calls restore directly; restore creates a staging directory, extracts the archive, and promotes the vault without the preflight used by creation. Matter contents have already entered the forbidden location even if some later operation rejects it.

**Confidence:** High. **Repair direction:** Apply location preflight before creating staging or extracting any bytes.

### R04 — Restore can delete a concurrently created vault even when overwrite is disabled.

**Locations:** `src/mootloop/engine/backup.py:320-334`.

**Failure scenario:** Restore sees an absent target at line 321 and starts extracting into staging. Another process creates and populates the target matter. Restore then observes that the target exists and calls `_rmtree(target)` unconditionally, deleting the new vault before promotion despite `overwrite=False`. Two concurrent restores to an initially absent target can also hit this race.

**Confidence:** High. **Repair direction:** Reserve the destination under shared creation/restore coordination and enforce the no-overwrite condition at promotion; a standalone second check still leaves a race.

### R05 — Matter close can destroy the vault before any durable off-vault record makes that destruction recoverable.

**Locations:** `src/mootloop/close.py:602-604`, `src/mootloop/close.py:638-670`.

**Failure scenario:** Retention checks, inventory, and backup succeed; `_purge_vault` removes the matter. The process dies, or the off-vault store fails, before `_append_tombstone` or `_write_close_record`. The original audit head and inventory exist only in memory at that point. Retry resolves an absent matter and fails before it can finish the close record. With the explicitly supported acknowledged backup skip, there is no backup from which to reconstruct the evidence either. A successful backup preserves content, but does not repair this closure transaction automatically.

**Confidence:** High. **Repair direction:** Durably record an off-vault close intent containing recovery inputs before purge, and allow retry to finish from that intent when the vault is absent.

### R06 — Concurrent first-use key generation returns different keys while persistence selects only the last one.

**Locations:** `src/mootloop/secrets.py:75-77`, `src/mootloop/secrets.py:115-153`, `src/mootloop/secrets.py:166-171`.

**Failure scenario:** On a local installation without a pre-seeded backup key, two processes both read “missing,” generate different keys, append duplicate key entries, and each returns its own key. Subsequent reads select the last duplicate. A backup encrypted by the other process cannot be decrypted using that selected key; manual recovery of the earlier entry would be required. Download signing-key creation has the same race, invalidating links issued with the losing value. The hosted pre-seeded path does not require this creation race.

**Confidence:** High. **Repair direction:** Serialize the entire read/create/persist transaction and return the committed winner; define duplicate-entry handling explicitly.

### R07 — Canary creation can leave usable vaults whose tokens are absent from the registry.

**Locations:** `src/mootloop/privacy.py:112-133`, `src/mootloop/privacy.py:153-168`, `src/mootloop/vault.py:362-369`.

**Failure scenario:** Two matter creations sharing one registry each load its old contents, add their own token, and write the whole registry. The last write loses the other token even though both creations can return successfully. Independently, a crash after `.canary` is written but before registry persistence leaves a populated vault without registration. Outbound checks enumerate registry keys, so a payload containing the missing token is no longer blocked by the canary check. This is a lost tripwire, not a claim that all matter text is normally detected by canaries.

**Confidence:** High. **Repair direction:** Use a locked atomic registry update and a recoverable creation transaction with an invariant verifying registration before a vault becomes usable.

### R08 — Cross-host restore preserves `.canary` but does not establish its destination-registry binding.

**Locations:** `src/mootloop/engine/backup.py:287-336`, `src/mootloop/privacy.py:120-133`, `src/mootloop/privacy.py:101-109`, `src/mootloop/privacy.py:153-168`.

**Failure scenario:** Restore a normal vault archive on a host whose valid canary registry does not contain the source host's token. Restore returns the extracted vault without registering its `.canary`. The hosted validator accepts a structurally valid empty registry, and the outbound checker tests only its entries; a payload containing the restored token passes that check. No malformed registry is needed. Unlike R07, this occurs without a crash or concurrency.

**Confidence:** High. **Repair direction:** Validate and durably bind the restored token to the destination registry before publishing the restored vault.

### R09 — Requests with the same number in different served sets are cross-wired during gating and export.

**Locations:** `src/mootloop/models/requests.py:66-71`, `src/mootloop/discovery_parser.py:144-154`, `src/mootloop/context.py:286-304`, `src/mootloop/orchestrator.py:683-686`, `src/mootloop/export/master.py:223-230`, `src/mootloop/export/master.py:250-252`.

**Failure scenario:** Load two interrogatory sets, each containing request 1 with different text. Both receive `ROG-1`; context accepts both sets. Turn slots distinguish their request indices, but the gate context selects the first matching request ID, evaluating the second response against the first request's text. Export builds a dictionary keyed only by request ID, so the later draft replaces the earlier one and is rendered for both sets. Opponent numbering can remain unchanged in display while internal identity includes the set.

**Confidence:** High. **Repair direction:** Carry set-qualified identity through turns, decisions, gates, anchors, and exports, or reject ambiguous input before launch until supported.

### R10 — Resolving a non-RFA decision can authorize export without implementing the attorney's chosen outcome.

**Locations:** `src/mootloop/decisions.py:131-197`, `src/mootloop/decisions.py:288-296`, `src/mootloop/export/master.py:107-156`, `src/mootloop/export/master.py:200-210`, `src/mootloop/gate_ledger.py:135-147`.

**Failure scenario:** A draft contains a privilege objection. The attorney selects `produce`, or selects `waive` for objection posture; the decision closes. The renderer consults selected keys only for RFA dispositions and still emits every original objection. Likewise, choosing `strike` for an unsupported assertion leaves the original non-RFA response text intact. Closing those decisions removes their blockers, so after the remaining gates and attestation are satisfied the contradictory text can be exported cleanly. This does not bypass the separate attestation requirement.

**Confidence:** High. **Repair direction:** Apply and review resolution-aware output changes, or keep export blocked where an outcome requires a revised draft rather than merely a closed decision.

### R11 — RFA export drops all drafted explanatory text, including the substance of a qualified admission.

**Locations:** `src/mootloop/export/master.py:58-62`, `src/mootloop/export/master.py:140-153`, `src/mootloop/export/master.py:262-267`, `src/mootloop/export/service.py:175-179`.

**Failure scenario:** A qualifying RFA draft says, for example, “Admit delivery on May 1; deny signed acceptance,” and its disposition is `qualify`. The renderer outputs only “Admitted in part and denied in part” plus objections. It never emits the RFA's `response_text`, so the exported document no longer identifies what is admitted or denied. Both combined and per-set masters use this renderer; clean DOCX is generated from the per-set masters.

**Confidence:** High. **Repair direction:** Preserve the reviewed response narrative and verify that any changed disposition remains consistent with it.

### R12 — RFP export asserts withholding solely because an objection exists.

**Locations:** `src/mootloop/export/master.py:45-50`, `src/mootloop/export/master.py:150-156`.

**Failure scenario:** A draft raises an objection but explicitly answers that all responsive documents will be produced and none are withheld. Export emits that response and then adds “Responsive materials are being withheld…” because `draft.objections` is nonempty. The generated filing contradicts its own substantive answer; the presence of an objection is not evidence of actual withholding.

**Confidence:** High. **Repair direction:** Derive this statement from an explicit reviewed withholding field, with consistency validation against the answer.

### R13 — A failed contribution write leaves an accepted learning that the acceptance API refuses to repair.

**Locations:** `src/mootloop/learn/routing.py:206-207`, `src/mootloop/learn/routing.py:272-303`, `src/mootloop/engine/launch.py:53-60`.

**Failure scenario:** `append_review` durably records acceptance, then `ContextContributionStore.put` fails or the process dies. The proposal now reports accepted, but future launches load contributions from the separate contribution store, where the learning is absent. Retrying the identical acceptance fails the “already has a final review” guard before it can publish the missing contribution. Recovery requires a separate repair, not the advertised operation retry.

**Confidence:** High. **Repair direction:** Make acceptance replay reconcile the exact expected contribution, or commit both through a recoverable operation record.

### R14 — Journal recovery treats completed corruption as a torn write and accepts unterminated records that the next append corrupts.

**Locations:** `src/mootloop/journal.py:81-90`, `src/mootloop/journal.py:214-234`, `src/mootloop/journal.py:280-286`.

**Failure scenario:** The last newline-terminated record is schema-invalid, such as a spend record with a damaged required field. The condition at line 221 raises only if bytes follow that newline; as the final line it is silently truncated and earlier state is returned, potentially undercounting spend. Conversely, a crash leaving a complete valid JSON event without its final newline causes the reader to return that event without repairing the separator. The next append joins another JSON object to it, producing invalid JSON; subsequent reading can discard both records or fail if more lines follow.

**Confidence:** High. **Repair direction:** Define newline termination as the commit boundary; fail closed on invalid complete records and perform any incomplete-tail repair under writer serialization. This is distinct from R02's concurrent truncation race.

### R15 — An interrupted decision append poisons all later decision reads and exact-resolution retries.

**Locations:** `src/mootloop/decisions.py:51-58`, `src/mootloop/decisions.py:75-86`, `src/mootloop/decisions.py:249-252`, `src/mootloop/gate_ledger.py:135`.

**Failure scenario:** A write error or crash leaves a partial final JSON line while generating or resolving a decision. `_records` parses every nonempty line with no incomplete-tail handling. Subsequent list, gate-ledger, and resolution calls raise validation errors before reaching the idempotent-resolution recovery branch. Even a retry of the exact request cannot repair the log through this service.

**Confidence:** High. **Repair direction:** Use a committed-line protocol with lock-protected incomplete-tail recovery, while still rejecting malformed complete records.

### R16 — SSE reconnection folds replayed events into existing state and double-counts live metrics.

**Locations:** `src/mootloop/web/api/sse.py:41-42`, `src/mootloop/web/api/sse.py:60-73`, `frontend/lib/api/useRunStream.ts:101-115`, `frontend/lib/api/runStream.ts:86-141`.

**Failure scenario:** A live connection receives one completed turn and its spend event, then suffers a transient network error. The client permits automatic reconnect; each server connection starts at byte zero and supplies no event IDs. The existing reducer increments completed turns and spend again and appends duplicate timeline entries. These are incorrect displayed totals; the server's journal spend is not doubled by this frontend defect.

**Confidence:** High. **Repair direction:** Establish stable event IDs and resumable cursors, or deduplicate/reset the client fold on reconnect. Cursor support must also cross the BFF's header allowlist (`frontend/app/api/[...path]/route.ts:20-27`).

### R17 — An unchecked worker ID escapes the heartbeat directory through path components.

**Locations:** `src/mootloop/cli/operations.py:26-45`, `src/mootloop/engine/worker.py:107`, `src/mootloop/engine/worker.py:132-143`, `src/mootloop/engine/worker.py:178-183`.

**Failure scenario:** Start a worker with `--worker-id ../../victim`. Joining that value beneath `<matters-root>/.queue/workers` selects `<matters-root>/victim.heartbeat`; the first tick overwrites that file with heartbeat JSON. An absolute worker ID can similarly discard the intended parent. This requires control of operator CLI/configuration input; it is not demonstrated as a remote unauthenticated exploit. The suffix and payload remain constrained.

**Confidence:** High. **Repair direction:** Validate worker IDs as identifiers before any queue/heartbeat use and enforce path containment.

### R18 — A restored directory/config identity mismatch causes matter listing and lookup to refer to different matters.

**Locations:** `src/mootloop/engine/backup.py:378-382`, `src/mootloop/registry.py:50-67`, `src/mootloop/registry.py:92-96`, `src/mootloop/registry.py:117-123`.

**Failure scenario:** An archive has top-level directory `a` but a valid `matter.yaml` declaring `matter_id: b`. Restore checks only that the configuration file exists. Registry enumeration returns identity `b` with relative path `a`, while `resolve("b")` uses directory `b`. Selecting the listed identity therefore either fails or opens a different existing vault named `b`; resolving `a` yields metadata declaring another identity. This requires a mismatched archive/configuration, not an ordinary correctly produced backup.

**Confidence:** High. **Repair direction:** Validate restored configuration and require directory, configuration, and registry identity to agree before promotion and enumeration.

### R19 — A structurally invalid local privacy registry silently becomes an empty policy.

**Locations:** `src/mootloop/privacy.py:65-79`, `src/mootloop/privacy.py:153-171`.

**Failure scenario:** A present local registry contains valid JSON such as `{"canaries": [], "denylist": {}}` after an incorrect edit or migration. `load_registry` substitutes empty collections for both invalid fields. A payload containing a formerly registered canary or denylisted value passes those tripwire checks instead of reporting invalid policy. Exact-secret checks still run. Hosted outbound loading already rejects this shape, so this finding concerns local-mode policy loading.

**Confidence:** High. **Repair direction:** Reject structurally invalid present registries consistently across privacy entry points; distinguish an explicitly valid empty policy from malformed policy.

## Coverage and exclusions

The breadth pass and subsequent failure-path inspection covered these source groups:

| Area | Modules/paths reviewed |
| --- | --- |
| Execution and persistence | `orchestrator.py`, `stages.py`, `provider_driver.py`, `journal.py`, `budget.py`, `pipeline.py`, `convergence.py`, `decisions.py`, `llm.py`, `persistence.py`, associated event/run models |
| Vault and security | `vault.py`, `registry.py`, `privacy.py`, `secrets.py` **source only**, `runtime.py`, `close.py`, `engine/{queue,worker,driver,launch,outbox,claude_provider,isolation,egress_exec,proxy_service,backup}.py` |
| Inputs and context | `ingest.py`, `conversion.py`, `conversion_client.py`, `facts.py`, `discovery_parser.py`, `context.py`, `context_assembly.py`, `context_memory.py`, `context_sources.py`, `migrations.py`, `config.py`, `taskspec.py`, `tasks.py`, `resources.py`, related domain models |
| Gates and analysis | All `citations/*.py` and `gates/*.py`, `gate_ledger.py`, `judge_profiles.py`, `production_suggestions.py`, `panels.py`, `oracles.py` |
| Output and learning | `attest.py`, `evidence.py`, all `export/*.py` and `learn/*.py` |
| Interfaces | All CLI adapters, web API routes/dependencies/readers/models/SSE, web security/audit, demo `web/app.py` and `web/bake.py`; frontend middleware, BFF, auth, API client/events/stream reducer, stores, substantive begin/inbox/cockpit/decision/attestation/export/learning/production paths |
| Unmerged U-13 | `strategy_board_store.py`, `models/strategy_board.py`, associated vault/close integration; no externally active approval or prompt-injection path assumed |

Static styling and presentational-only frontend assets, external dependencies, deployment configuration, other local branches, production state, credentials, and real archives were not audited. Source review cannot establish whether any triggering condition has occurred in operation. No claim of exhaustive defect absence is made for modules without findings.

Candidates rejected during challenge included an alleged missing post-reservation budget check (prompt assembly calls `find_spec` and `plan_next`, which recheck the cap), a normal hosted config-directory placement claim (the provisioning wrapper validates root separation), and an alleged promotion without durable human authorization (the firm event stores its review). Public helper misuse without a demonstrated caller and intentional content-replacement possibilities were not elevated into findings.
