# FD6-01 Encrypted Off-Box Backup Decision Packet

Date prepared: 2026-08-24

Status: awaiting provider, records-retention, RPO/RTO, and restore-drill approval

This packet authorizes no account creation, credential issuance, upload, protected-matter
access, or restore. It is deliberately separate from D-09: D-09 protects content-free
integrity heads, while FD6-01 must preserve and restore encrypted matter snapshots.

## Recommendation

Use a private Backblaze B2 bucket in a dedicated backup account, separate from the
MootLoop host, Cloudflare edge, and the AWS accounts proposed for D-09. Enable B2
Object Lock with **Compliance-mode** default retention and Backblaze-managed
server-side encryption before the first upload. Continue to encrypt every archive
locally with MootLoop's existing AES-256-GCM backup key; server-side encryption is
defense in depth, not a replacement for client-side encryption.

Backblaze is recommended here because it provides an independent failure and account
boundary, S3-compatible access, Object Lock, hot retrieval, prefix-scoped application
keys, and straightforward restore economics. Its current published pay-as-you-go
price starts at $6.95/TB/month, with no minimum storage duration and free egress up to
three times average monthly storage. Price is an estimate, not a contractual control;
confirm it at provisioning.

Official references:

- [Backblaze B2 pricing](https://www.backblaze.com/cloud-storage/pricing)
- [Backblaze Object Lock](https://www.backblaze.com/docs/cloud-storage-enable-object-lock-with-the-native-api)
- [Backblaze application keys](https://www.backblaze.com/docs/en/cloud-storage-application-keys)
- [Backblaze application-key capabilities](https://www.backblaze.com/docs/cloud-storage-application-key-capabilities)
- [Backblaze server-side encryption](https://www.backblaze.com/docs/cloud-storage-server-side-encryption)
- [Backblaze lifecycle rules and Object Lock](https://www.backblaze.com/docs/en/cloud-storage-lifecycle-rules)
- [Backblaze S3-compatible integration](https://www.backblaze.com/docs/en/cloud-storage-get-started-with-a-backblaze-integration)

## Data and metadata boundary

The remote service receives only already-encrypted `.tar.gz.enc` bytes,
client-encrypted recovery-catalog ciphertext, ciphertext size and digest, opaque
object keys, object-version identifiers, retention and legal-hold metadata,
server-side-encryption metadata, and operational timestamps. It never receives
plaintext matter data, readable matter IDs, party names, source filenames, the backup
key, or D-09 signing keys.

Existing local archives include the readable matter ID in their filename. They must
not be uploaded under that name. The remote key is exactly:

`archives/<opaque-scope>/<UTC-compact-timestamp>-<ciphertext-sha256>.tar.gz.enc`

`opaque-scope` is derived with a backup-specific HMAC key that is separate from the
archive-encryption key and the D-09 scope key. The local audit journal retains the
mapping, but it is not the disaster-recovery authority. Each accepted backup also
updates a versioned, client-encrypted recovery catalog under the fixed
`recovery-catalogs/` prefix. Inside its authenticated ciphertext, the catalog maps the
readable matter ID to the opaque object key, archive-key identifier, ciphertext
digest, byte length, retention, and accepted object-version identifier. Bucket and
account names and plaintext catalog metadata contain no client or matter information.

## Identities and key custody

Use separately issued, non-interchangeable credentials:

- **Uploader:** restricted to the one bucket and `archives/` prefix, with only the
  capabilities needed to create objects. It receives no delete, retention-change,
  legal-hold, lifecycle, bucket-administration, key-administration, or account-wide
  listing capability.
- **Verifier/restorer:** stored off the MootLoop host and restricted to listing and
  reading object versions, retention, and encryption metadata from that bucket. It
  receives no write, delete, retention-change, lifecycle, bucket, or key-management
  capability.
- **Retention administrator:** separately controlled; configures the bucket default,
  applies or releases legal holds under the firm's records process, and never runs on
  the MootLoop host.

The client-side AES-256-GCM archive key remains in the approved MootLoop secret store
and a separately controlled offline escrow. It is never stored in B2 or beside a
downloaded archive. Retiring keys remain recoverable for every retained archive they
sealed; rotation does not silently make retained backups unreadable.

The recovery-catalog encryption key and every version of the backup-specific HMAC key
also have separately controlled offline escrow. They are distinct from every archive
key. Losing either must fail the backup health gate because intact remote ciphertext
would no longer be reliably discoverable after total host loss.

Account-owner access is never automated or stored on the MootLoop host. Protect it
with the strongest MFA Backblaze supports, a distinct monitored administrative
mailbox, offline recovery material, and audited break-glass use. Inventory uploader,
verifier/restorer, retention-administrator, and account-owner credentials; store them
only in their approved custody boundary; rotate them on a documented cadence; and
revoke them immediately on suspected compromise. Provisioning must verify the exact
current B2 application-key capabilities before any credential is issued.

## Upload and acceptance contract

1. Create and locally verify the lock-consistent encrypted snapshot using the existing
   `backup_matter` path.
2. Before network I/O, durably record the expected opaque key, ciphertext SHA-256,
   byte length, archive-key identifier, creation time, required retain-until time,
   approved legal-hold state, and `remote_backup_pending` state.
3. Upload the exact ciphertext once under the deterministic key over TLS. A response
   lost after the request might have reached B2, so that ambiguous PUT is never retried
   automatically and never receives a new key or timestamp. Reconciliation runs first.
4. The off-host verifier enumerates every version for the exact key and downloads the
   candidate ciphertext. Zero versions, more than one version, or any non-matching
   version is a blocking incident; later scheduled backups use their own new recovery
   point and do not erase or disguise the pending attempt.
5. When the required retain-until date exceeds the bucket default, the separately
   controlled retention administrator uses a short-lived, object-scoped credential to
   extend that exact version in Compliance mode before acceptance. The administrator
   also applies any approved litigation hold before acceptance. The uploader never
   receives retention-change or legal-hold authority.
6. The verifier confirms the single candidate's expected byte length, SHA-256,
   Compliance retain-until date, legal-hold state, and server-side encryption.
7. Create an authenticated, client-encrypted recovery-catalog snapshot containing the
   accepted mapping, upload it under a content-addressed key in `recovery-catalogs/`,
   and subject it to the same Compliance retention and exact-version verification.
8. Only then may the local journal record `remote_backup_verified`.

Authentication failure, timeout, ambiguous success, or verification failure leaves
the backup pending and keeps the local archive. It must never be reported as an
off-box recovery point until exact verification succeeds.

## Retention, legal holds, and deletion

The proposed default is **seven years** in Compliance mode, subject to the firm's
governing records schedule. Each archive's required retain-until date is the later of
the bucket default and the matter's approved destruction date. A litigation hold is a
separate legal-hold control and prevents deletion until the authorized records process
releases it.

Object Lock prevents premature provider deletion; it does not authorize over-retention.
After both the approved destruction date and every legal hold have cleared, a separate
retention job may delete expired versions under a short-lived deletion credential and
record the outcome. The always-on uploader and verifier never receive delete authority.
If client-key destruction is used as an additional cryptographic-erasure step, it must
be separately approved only after proving no still-retained archive depends on that
key.

## RPO, RTO, and restore proof

- **RPO:** nightly for active matters, on demand before risky operations, and always
  before matter close. A missed or unverified upload makes the effective RPO older and
  visible; it never silently advances.
- **RTO target:** 24 hours for a single matter. B2 remains hot storage, avoiding an
  archive-rehydration delay before download.
- **Integrity exercise:** monthly automated content-free inventory and ciphertext
  verification; quarterly synthetic remote restore into an isolated scratch root.
- **Operational exercise:** annual authorized restore of a representative retained
  archive, or after provider/credential/key-path changes. Any protected archive drill
  requires fresh named authorization and an isolated non-production destination.

The remote drill passes only when the downloaded ciphertext digest matches the
accepted remote record, the archive decrypts with the escrowed key, restore is
traversal-safe and fail-closed, and every non-transient restored byte matches the
source inventory. Wrong-key and tamper cases must still fail without a partial vault.
At least annually, the drill starts without the local audit journal or ordinary host
secret store and proves that the off-host recovery catalog plus offline escrow can
locate and restore the named archive within the RTO.

## Rejected alternatives

- **A local sync folder:** forbidden by the vault boundary and not an independent
  off-box control.
- **Cloudflare R2 as the only backup:** workable, but it concentrates edge and backup
  administration in the existing Cloudflare estate and its bucket-lock rule remains
  removable by an authorized configuration administrator.
- **The D-09 AWS accounts:** combining protected encrypted backups with content-free
  integrity records expands their trust and data scope and defeats the separation the
  D-09 design relies on.
- **S3 Glacier Deep Archive as the primary copy:** lower storage cost but adds restore
  initiation and retrieval delay. It can be considered later as a second archive tier,
  not as the only copy supporting the 24-hour single-matter RTO.

## Approval requested

Choose one inline:

- **FD6-01P A (recommended only if it matches the governing firm records schedule):**
  Backblaze B2 architecture above, seven-year Compliance default, nightly backups for
  active matters, on-demand backups before risky operations, a backup before matter
  close, 24-hour RTO, quarterly synthetic drill, and annual authorized representative
  restore. Choosing A confirms that the firm schedule authorizes seven years.
- **FD6-01P B:** the same Backblaze architecture with another exact retention period,
  RPO, RTO, or drill cadence; state the changed values.
- **FD6-01P C:** do not use Backblaze; name the preferred provider and any required
  account-boundary constraint so a provider-specific packet can be prepared before
  implementation.

After approval, implementation may add the opaque-key uploader, verifier, pending
state, recovery reconciliation, and synthetic remote restore tests. Account creation,
credentials, billing, protected-data upload, and any representative live restore stay
separately visible operator actions. Fresh D-03 authorization remains required before
any named protected-matter read, upload, or restore drill.
