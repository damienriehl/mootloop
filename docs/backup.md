# MootLoop backup & restore (FD-6 hosted-backup gate)

The supported backup path is an **idle-only, lock-consistent, encrypted-at-rest** snapshot of
a matter vault, pushed **off-box** to a remote target. This runbook is the operational
contract; the code is `src/mootloop/engine/backup.py` (`backup_matter` / `restore_matter`).

## What a snapshot is

- A `tar.gz` of the whole vault, **excluding** `staging/` (transient working dir).
- Taken while the per-matter `RunLock` is briefly held, so it never races a live run's writes.
- Refused if the destination is inside a background-sync folder or a git repo.
- Read back after writing (decrypt-and-verify the archive lists `matter.yaml`) before the path is returned — a snapshot that cannot be verified is deleted, never reported as success.

## Encryption posture

- Algorithm: **AES-256-GCM**. A fresh random 12-byte nonce is generated per snapshot and prepended to the ciphertext; the GCM tag authenticates the whole archive.
- On-disk artifact: `<matter_id>-<compact_ts>.tar.gz.enc`. This is the hosted default; `encrypt=False` (plaintext `.tar.gz`) exists only for the local/demo tier.
- The intermediate plaintext tar.gz is written to a same-dir temp file, encrypted to the final `.enc`, and then **shredded** (overwrite + unlink) in a `finally` — it never lingers on disk.
- Key: `MOOTLOOP_BACKUP_KEY`, 32 raw bytes stored base64/urlsafe in `~/.mootloop/secrets.env`.
- The key value is registered for redaction (`mootloop.secrets`) and is never logged.

## Key management (read this)

- **Pre-seed the key on the box.** `~/.mootloop` is mounted **read-only** in the `api`/`driver` containers, so first-use auto-derivation would fail closed. Generate the key on the host as the `mootloop` user and write it to `~mootloop/.mootloop/secrets.env` (dir 0700, file 0600) before any backup runs — same discipline as `MOOTLOOP_DOWNLOAD_SIGNING_KEY`.
- Mint one with: `python -c "import base64,os;print('MOOTLOOP_BACKUP_KEY='+base64.urlsafe_b64encode(os.urandom(32)).decode())" >> ~/.mootloop/secrets.env` (run as `mootloop`; then `chmod 600`).
- **NEVER back up the key alongside the data.** The backup key must live only in the secrets mount (and your offline key escrow). If the key is stored in the same remote bucket as the archives, an attacker with the bucket has both — encryption-at-rest buys nothing.
- **Losing the key means losing every archive it sealed.** Escrow it separately (password manager / offline vault). Rotating the key does not re-encrypt old archives; keep the retiring key as long as you keep archives sealed with it.
- The key is never written to the repo, a vault, or a backup archive, and never appears in logs or test fixtures (tests mint ephemeral keys).

## Off-box push (sync-folder-guard-compliant)

- `backup_matter` writes only to a **local** destination dir; it refuses sync-folder / git-repo destinations. Push the finished `.enc` off-box as a **separate** step.
- Use `rclone` (or `rsync`) to a remote that is **not** a local background-sync mount:
  - `rclone copy /srv/mootloop-backups/<matter>-<ts>.tar.gz.enc remote:mootloop-backups/ --immutable`
  - or `rsync -a --chmod=600 /srv/mootloop-backups/<file>.enc backup-host:/srv/mootloop-archive/`
- The remote should be write-once / versioned (e.g. object-lock bucket) so a compromised box cannot delete history.
- Do **not** push into Dropbox/Google Drive/iCloud/OneDrive local folders — the guard blocks those as snapshot destinations, and they must not be the off-box target either.

## RPO (recovery point objective)

- **Cadence: on demand + automatically before every matter close** (`mootloop close` takes a fresh pre-purge backup unless explicitly acknowledged-skipped).
- **Recommended standing cadence for active matters: nightly** (cron the `mootloop backup` + off-box push while the vault is idle).
- **Stated RPO = the last successful snapshot.** With the nightly + pre-close cadence, worst-case data loss is one day of work; on-demand snapshots before risky operations tighten it further. There is no continuous replication — this is a snapshot RPO, not an RTO-zero mirror.

## Restore procedure

- Restore is fail-closed and traversal-safe: a wrong key, a truncated file, or a tampered GCM tag raises `BackupError`; malicious tar members (`..`, absolute paths, symlink/hardlink escapes) are rejected before anything is written, and extraction lands in a staging dir promoted only on success — never a partial extract.
- Pull the archive back on-box first, then:
  - `mootloop restore <matter>-<ts>.tar.gz.enc --matters-root /srv/mootloop-matters`
  - Add `--overwrite` only to replace an existing non-empty vault (refused by default).
- The key must be present in the secrets mount (same `MOOTLOOP_BACKUP_KEY`) for restore to decrypt.
- Verify after restore: `mootloop validate` on the restored vault; confirm the expected run/deliverable tree is present.

## Restore drill

- The load-bearing proof is `tests/unit/test_engine_backup.py`: create → encrypted backup → restore into a fresh matters-root → assert the tree byte-matches the source (minus `staging/`), plus the fail-closed cases (wrong key, single-byte tamper, `..`/absolute/symlink members).
- Run it before trusting a new box: `uv run pytest tests/unit/test_engine_backup.py`.
- Periodically run a **live** drill: restore a real archive into a scratch matters-root and diff against the source before relying on the backups.
