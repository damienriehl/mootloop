# D-09 Remote Signed-Head Sink Decision Packet

Date prepared: 2026-08-23

Status: awaiting two-account AWS architecture and retention approval

This packet completes the concrete follow-up required by D-09. It authorizes no
remote write and contains no credentials, protected matter identifiers, or matter
content. D-09 covers only the content-free integrity ledger. It neither authorizes nor
closes FD6-01's separate encrypted off-box backup, provisioning, upload, or restore
obligation.

## Recommendation

Use a concrete two-account AWS architecture:

1. a private primary S3 general-purpose bucket in a dedicated signed-head AWS account;
   and
2. a private verifier-receipt S3 general-purpose bucket in a separately controlled AWS
   account that the MootLoop writer and primary-account administrator cannot control.

Both buckets have Versioning and S3 Object Lock enabled at creation, block all public
access, require TLS and server-side encryption, and apply the same approved
bucket-default **Compliance-mode** retention period to every object. The primary
bucket stores only signed integrity heads: opaque HMAC-derived scope identifiers,
sequence number, prior-head digest, current-head digest, signing-key identifier,
signature, and UTC timestamps. The receipt bucket stores only signed content-free
verification receipts for those heads. Neither bucket stores matter text, filenames,
party names, readable matter IDs, prompts, drafts, exports, or encrypted backups.

S3 Object Lock is recommended because its Compliance mode prevents a protected object
version from being overwritten or deleted by any AWS identity, including the account
root, and prevents shortening its retention period. Separating the signed heads and
verifier receipts across accounts prevents either mandatory remote component from
depending on an unnamed sink or the writer's control plane.

Cloudflare R2 is rejected for this packet rather than offered as an approval option.
An authorized configuration administrator can remove an R2 Bucket Lock rule, and an
R2 Object Read & Write credential permits reading, writing, and listing objects rather
than put-only access. A defensible R2 design would require a protected off-host broker
to hold the parent secret and locally issue short-lived, action-scoped credentials for
each exact `PutObject` path, plus an R2-specific receipt and recovery design. That is a
second architecture and is not approved or specified here.

Official references:

- [AWS S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [AWS S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [Cloudflare R2 Bucket Locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/)
- [Cloudflare R2 API-token permissions](https://developers.cloudflare.com/r2/api/tokens/)
- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)

## Accounts, APIs, and authentication

Provision each bucket and its retention configuration through the administrator of its
own AWS account. Account policy keeps the primary signed-head administrator, the
verifier-receipt administrator, and the MootLoop runtime under separate control.

The MootLoop runtime receives a dedicated AWS IAM access key from its approved secrets
store and uses only the S3 `PutObject` API against the primary bucket's fixed
`signed-heads/` prefix.

The runtime identity must be allowed only:

- `s3:PutObject` for `arn:aws:s3:::<approved-bucket>/signed-heads/*`;
- TLS-only requests; and
- server-side encryption and checksum headers required by bucket policy.

It must not receive list, read, delete, multipart-delete, bucket-configuration,
versioning, retention, legal-hold, replication, lifecycle, policy, or
`s3:BypassGovernanceRetention` permissions. The bucket must block public access and
must reject writes outside the fixed prefix.

Each logical scope has one serialized sequence allocator protected by its local
append lock. Before network I/O, the allocator durably records the exact canonical
bytes, byte digest, signature, opaque scope, next sequence, prior-head digest,
expected key, minimum retain-until date, and `remote_anchor_pending` state. The stable
scope identifier is derived with a dedicated scope-HMAC key whose custody and backup
are separate from signing-key rotation. The canonical object key is exactly
`signed-heads/<opaque-scope>/<20-digit-zero-padded-sequence>.json`; a run digest may be
signed inside the object but never changes its key. The client sends
`If-None-Match: *`. Concurrent or split-brain writers for one sequence therefore
contend on the same key instead of creating two independently writable branches.

The signing private key remains separate from AWS credentials in the OS keychain.
The remote object contains only the public key identifier and signature. A
compromised host may append false future entries while it controls both credentials,
but it cannot rewrite or delete already retained history.

Verification is performed off the MootLoop host by a separately controlled principal.
It uses three non-interchangeable identities:

- **Primary reader:** `s3:ListBucketVersions` on the primary bucket constrained to the
  fixed `signed-heads/` prefix, plus `s3:GetObjectVersion` and
  `s3:GetObjectRetention` for versions under that prefix. It has no primary-bucket
  writes, deletes, tags, holds, retention writes, lifecycle, policy, replication,
  versioning, or configuration permissions.
- **Receipt writer:** only `s3:PutObject` with `If-None-Match: *` against
  `arn:aws:s3:::<approved-receipt-bucket>/verifier-receipts/*`. Receipt-bucket policy
  rejects writes outside that prefix and requests missing the required TLS,
  encryption, checksum, or conditional-write headers. This identity cannot list,
  read, delete, tag, configure, alter retention or legal holds, manage lifecycle or
  versioning, or bypass retention.
- **Recovery auditor:** a separate identity with `s3:ListBucketVersions` on the
  receipt bucket constrained to `verifier-receipts/`, plus `s3:GetObjectVersion` and
  `s3:GetObjectRetention` for versions under that prefix. It has no receipt-bucket
  writes, deletes, tags, holds, retention writes, lifecycle, policy, replication,
  versioning, configuration, or retention-bypass permissions.

The verifier signs canonical content-free receipt bytes containing the opaque scope,
zero-padded sequence, primary object key, canonical-head digest, head signing-key
identifier, primary S3 version ID, primary retention mode and retain-until date,
observation time, and prior receipt digest. Its deterministic receipt key is exactly
`verifier-receipts/<opaque-scope>/<20-digit-zero-padded-sequence>.json`. The receipt
writer uploads those exact bytes without changing the key or signature.

Every accepted anchor requires its exact receipt to be stored synchronously in the
verifier-receipt bucket with Compliance-mode retention for the approved period. The
receipt write succeeds only after S3 returns or reconciliation recovers its version
ID, and the recovery auditor confirms that exact version's canonical receipt bytes,
verifier signature, checksum, Compliance mode, and retain-until date. An accepted head
therefore has zero checkpoint staleness. The verifier also performs a full two-bucket
consistency sweep at least every 24 hours. If the newest valid retained receipt or
full-chain sweep is older than 24 hours, the current chain is reported as stale and
must not be described as currently host-writer-resistant. Recovery trusts the last
receipt version whose verifier signature, receipt chain, checksum, and retention
validate; any newer primary, receipt, or local entries remain untrusted until
independently admitted.

## Retention and immutability guarantee

Enable Versioning and Object Lock when creating both buckets, then configure the same
bucket-default Compliance-mode retention period on both before issuing any runtime,
verifier, receipt-writer, or recovery-auditor credential. The exact period is a firm
records-policy judgment. The proposed default is **seven years**, subject to the
firm's governing retention schedule. Object keys and versions are never reused, and
neither account may configure a lifecycle rule that deletes retained versions before
expiration.

## Fail-closed behavior

An attestation/export may be locally valid while the remote service is temporarily
unavailable, but it must remain explicitly `remote_anchor_pending` and must never be
described as host-writer-resistant. The anchoring operation succeeds only after:

1. the durable expected-object record is fsynced before upload;
2. the exact canonical signed-head bytes are uploaded under the expected key;
3. S3 returns success and a version ID, or the verifier reconciles an ambiguous
   outcome to exactly one acceptable version;
4. the off-host verifier admits the record into the existing scope chain and confirms
   the exact bytes, signature, version ID, Compliance mode, and retain-until date;
5. the verifier's signed receipt is retained in the separately controlled receipt
   bucket and its exact version is confirmed by the recovery auditor; and
6. the local append-only journal records that receipt without matter content.

Authentication failure, timeout, HTTP 409/412, checksum mismatch, missing retention
metadata, or key collision leaves the anchor pending while reconciliation runs.
Retries reuse only the durable expected canonical bytes and object key. They never
allocate another sequence or generate a new signature merely to hide an ambiguous
prior attempt.

For a timeout, 409, or 412, the writer does not interpret the response as success or
failure. The off-host verifier enumerates every version and delete marker for the
exact key, retrieves the candidate version bytes, recomputes their digest, verifies
the signed record, and reads version-specific retention. Reconciliation accepts only
one non-delete version whose bytes exactly match the durable expected digest and
signature, whose record matches the expected scope, sequence, and prior head, and
whose version is protected by Compliance mode through at least the expected date.
Zero matches, multiple matches, any conflicting version, any delete marker, or any
retention mismatch is a blocking integrity incident. Only an accepted reconciliation
may recover the actual S3 version ID and produce the external verifier receipt.

Receipt PUTs use the same durable expected-object and ambiguity contract. Before its
PUT, the off-host verifier durably records the exact receipt key, canonical bytes,
signature, checksum, and minimum retain-until date. A timeout, 409, or 412 leaves the
receipt pending; the receipt writer never changes the key, bytes, or signature. The
recovery auditor enumerates every version and delete marker for the exact receipt key
and accepts only one non-delete version whose bytes, verifier signature, checksum,
opaque scope, sequence, prior receipt digest, Compliance mode, and retain-until date
match the expected record. Zero matches, multiple matches, a conflicting version, a
delete marker, or a retention mismatch is a blocking integrity incident. Only that
exact-match reconciliation may recover the receipt-bucket version ID and complete the
anchor.

## Recovery and verification

The off-host verifier is the chain-admission authority, not merely a read-after-write
checker. For each scope it admits only the next zero-padded sequence, the exact prior
admitted head, the currently active signing key, one canonical object version, and a
valid Compliance retain-until date. A stale allocator, duplicate sequence, skipped
sequence, wrong prior digest, conflicting version, delete marker, or unadmitted key
state remains pending and becomes a blocking integrity incident; nothing uses
last-write-wins or repairs the chain automatically.

Recovery uses the primary-reader and recovery-auditor identities to enumerate every
version and delete marker under both fixed prefixes and retrieve signed heads and
receipts. It folds heads and receipts by opaque scope and sequence, verifies canonical
bytes, every signature and prior link, key state, checksums, version-specific
retention, and one-to-one head/receipt admission, then compares the admitted remote
head with the local journal and backup inventory. A fork, gap, stale or unexpected
key, missing or conflicting receipt, retention downgrade, stale checkpoint, or
local/remote divergence is a blocking integrity incident rather than an automatic
repair. A mutable or stale local journal or backup never overrides the last valid
retained receipt.

Signing-key rotation is an explicit chain state transition. Its canonical record
contains `record_type`, schema and algorithm versions, old and new key identifiers,
both public-key digests, effective scope and sequence, prior-head digest, reason, UTC
time, and signatures by both old and new private keys over the same unsigned bytes.
The old key signs through the rotation sequence and is rejected afterward; the new
key is rejected before that sequence and becomes active only for the following head.
The verifier must checkpoint the rotation record externally before admitting a head
under the new key. A compromised-old-key emergency rotation cannot rely on the old
signature alone and requires a separately approved, out-of-band verifier/admin
recovery record.

Retired public keys and their authenticated state records remain available for the
lifetime of retained heads. Retired private keys are destroyed after the approved
overlap and external rotation checkpoint. Any protected escrow of a retired private
key requires separate explicit approval, documented necessity, non-exportable or
equivalently protected custody, and an emergency-reactivation rule that the retired
key cannot authorize by itself. AWS administrator recovery credentials, verifier
receipt keys, active signing keys, and any separately approved escrow remain in
separate custody.

## Approval requested

Choose one inline:

- **D-09P A (recommended):** the two-account AWS S3 Object Lock architecture above,
  with Compliance mode and a seven-year default on both buckets.
- **D-09P B:** the same two-account AWS S3 Object Lock architecture and Compliance
  mode, with another exact approved retention period applied identically to both
  buckets.

Implementation and provisioning remain blocked until this named provider/retention
choice is explicitly approved. Provisioning credentials, bucket creation, and any
remote write will be handled as separately visible operator actions. This approval is
limited to the content-free D-09 integrity ledger and grants no FD6-01 encrypted
off-box backup authority.
