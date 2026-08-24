# D-09 Remote Signed-Head Sink Decision Packet

Date prepared: 2026-08-23

Status: awaiting provider and retention approval

This packet completes the concrete follow-up required by D-09. It authorizes no
remote write and contains no credentials, protected matter identifiers, or matter
content. D-09 covers only the content-free integrity ledger. It neither authorizes nor
closes FD6-01's separate encrypted off-box backup, provisioning, upload, or restore
obligation.

## Recommendation

Use a dedicated private AWS S3 general-purpose bucket with Versioning and S3 Object
Lock enabled. Apply a bucket-default **Compliance-mode** retention period to every
object. Store only signed integrity heads: opaque HMAC-derived scope identifiers,
sequence number, prior-head digest, current-head digest, signing-key identifier,
signature, and UTC timestamps. Never store matter text, filenames, party names,
readable matter IDs, prompts, drafts, or exports in this ledger.

S3 Object Lock is the recommended provider because its Compliance mode prevents a
protected object version from being overwritten or deleted by any AWS identity,
including the account root, and prevents shortening its retention period. Cloudflare
R2 Bucket Locks are an alternative in the existing Cloudflare estate, but an
authorized configuration administrator can remove a lock rule. R2's standard Object
Read & Write credential also permits reading, writing, and listing objects; it is not
a put-only runtime credential. A put-only R2 design would require a protected
off-host credential broker to retain the parent secret and locally issue short-lived
temporary credentials scoped to the `PutObject` action and the exact object path.
That extra high-trust broker and R2's removable lock rule make R2 less preferred.

Official references:

- [AWS S3 Object Lock](https://docs.aws.amazon.com/AmazonS3/latest/userguide/object-lock.html)
- [AWS S3 conditional writes](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html)
- [Cloudflare R2 Bucket Locks](https://developers.cloudflare.com/r2/buckets/bucket-locks/)
- [Cloudflare R2 API-token permissions](https://developers.cloudflare.com/r2/api/tokens/)
- [Cloudflare R2 temporary credentials](https://developers.cloudflare.com/r2/api/s3/temporary-credentials/)

## API and authentication path

Provision the bucket and its retention configuration through a separately controlled
administrator identity. The MootLoop runtime receives a dedicated AWS IAM access key
from its approved secrets store and uses only the S3 `PutObject` API against one fixed
bucket and prefix.

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
Its AWS identity is restricted to `s3:ListBucketVersions` on the fixed
`signed-heads/` prefix plus `s3:GetObjectVersion` and `s3:GetObjectRetention` for
versions under that prefix. It receives no put, delete, tagging, legal-hold,
retention-write, lifecycle, policy, replication, versioning, or bucket-configuration
permission. The verifier signs a content-free receipt containing the scope, sequence,
object key, canonical-byte digest, signing-key identifier, S3 version ID, retention
mode, retain-until date, observation time, and prior receipt digest. Those receipts
are stored outside both the writer host and the signed-head sink account under a
separately controlled append-only policy.

Every accepted anchor requires its exact verifier receipt synchronously, so an
accepted head has zero checkpoint staleness. The verifier also performs a full-chain
consistency sweep at least every 24 hours. If the newest valid external receipt or
full-chain sweep is older than 24 hours, the current chain is reported as stale and
must not be described as currently host-writer-resistant. Recovery trusts the last
externally stored receipt whose verifier signature and receipt chain validate; any
newer remote or local entries remain untrusted until independently admitted.

## Retention and immutability guarantee

Enable Versioning and Object Lock at bucket creation, then configure a bucket-default
Compliance-mode retention period before issuing the runtime credential. The exact
period is a firm records-policy judgment. The proposed default is **seven years**,
subject to the firm's governing retention schedule. Object keys and versions are
never reused, and no lifecycle rule may delete retained versions before expiration.

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
5. the verifier's signed receipt is durably stored outside the writer host and sink
   account; and
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

## Recovery and verification

The off-host verifier is the chain-admission authority, not merely a read-after-write
checker. For each scope it admits only the next zero-padded sequence, the exact prior
admitted head, the currently active signing key, one canonical object version, and a
valid Compliance retain-until date. A stale allocator, duplicate sequence, skipped
sequence, wrong prior digest, conflicting version, delete marker, or unadmitted key
state remains pending and becomes a blocking integrity incident; nothing uses
last-write-wins or repairs the chain automatically.

Recovery uses the same off-host read-only principal to enumerate every object version
and delete marker and retrieve signed heads. It folds heads by opaque scope and
sequence, verifies canonical bytes, every signature and prior-head link, key state,
retention, and the external verifier-receipt chain, then compares the admitted remote
head with the local journal and backup inventory. A fork, gap, stale or unexpected
key, retention downgrade, stale external checkpoint, or local/remote divergence is a
blocking integrity incident rather than an automatic repair. A mutable or stale local
journal or backup never overrides the last valid external receipt.

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

- **D-09P A (recommended):** AWS S3 Object Lock, Compliance mode, seven-year default.
- **D-09P B:** AWS S3 Object Lock, Compliance mode, with another exact retention period.
- **D-09P C:** Cloudflare R2 Bucket Lock, indefinite retention, accepting that an
  authorized configuration administrator can remove the lock rule and that put-only
  runtime access requires the protected off-host temporary-credential broker above.

Implementation and provisioning remain blocked until this named provider/retention
choice is explicitly approved. Provisioning credentials, bucket creation, and any
remote write will be handled as separately visible operator actions. This approval is
limited to the content-free D-09 integrity ledger and grants no FD6-01 encrypted
off-box backup authority.
