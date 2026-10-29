# Local BAT error records (rollout step 5)

Connector/session failures and native Fleet Monitor/tunnel failures use the shared
`ts`, `machine_id`, `component`, `version`, `level`, `code`, `msg`, `fingerprint`,
`count`, `context` JSON Lines record. Existing operations, retries, and ownership
gates keep their original behavior. This adds no upload, Hub API, issue filer,
BAT server integration, or deployment action.

The destination is `BAT_ERROR_LOG` when set, otherwise `~/.bat/logs/errors.jsonl`
on Linux/macOS or `%LOCALAPPDATA%\BAT\logs\errors.jsonl` on Windows. The active
file and four numbered backups each have a 10 MiB rotation threshold. Both writers
serialize threads and use the adjacent `.errors.jsonl.lock` for process locking.
Identical fingerprints update the first record's count for a fixed 60-second
window, retaining its original timestamp. Collapse updates replace the active
file atomically; consumers must reopen the pathname rather than retain an inode.
Logging I/O errors are discarded and never change the operation's result.
Unix log and lock files are created with mode 0600. Existing code-only Fleet
Monitor diagnostics remain available.

`src/bat_agent_connector/errlog_patterns.json` is packaged with Python and embedded
in fleet-core at compile time. Redaction applies to messages and recursively to
context keys/values; credential-shaped context keys have their entire values
removed. Fingerprints use redacted text. The ordered normalization expressions
in this file replace UUIDs with `<uuid>`, absolute Unix/Windows drive paths with
`<path>`, hexadecimal values (`0x…` or eight or more hex digits) with `<hex>`,
colon-prefixed ports with `:<port>`, then numbers with `<n>`. SHA-256 hashes the
UTF-8 string `component|code|normalized_msg`, retaining the first 16 lowercase hex
digits. No whitespace or case folding is performed. The external proposal lists
normalization categories without expressions; these expressions and
`tests/fixtures/errlog.json` make the BAT interpretation explicit and test parity
in both languages.

Python emits connector connection/authentication/invoke errors and session
start/resume/reconnect/invoke failures. Tunnels are owned by native Fleet, where
existing monitor failure events and failed tunnel respawns emit structured
records. Successful tunnel adoption is excluded. Tests use synthetic credentials,
temporary files, and simulated connection failures without live service writes.
