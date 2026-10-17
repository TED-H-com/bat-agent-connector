# Connector automatic updates / Connector 自動更新

Connector **0.2.7** includes automatic updates. Start with the
[published preview](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.7-preview.20261017)
and its matching source manifest and checksums.

For an existing per-user Linux installation, an operator first installs a release
containing `batc-update` and retains its `rollout-receipt.json`. Enrollment verifies
the receipt, installed source, canonical skill and the published release channel.
It refuses unknown/customized installations rather than replacing their files.

```bash
batc-update enable --channel preview --receipt /path/to/rollout-receipt.json
batc-update schedule
batc-update status
```

`preview` includes prereleases; `stable` accepts only published stable releases.
Choose the channel matching the bootstrap package. The user systemd timer checks
hourly with up to 15 minutes of jitter. A headless host needs a lingering user
manager to keep that timer running after logout; check the host's user-service
configuration. The updater does not change login policy itself.

`batc-update check` only checks the release feed. `batc-update run` applies the
enabled policy now. `batc-update disable` prevents further automatic installation,
including a prepared update whose activation was interrupted. The timer may still
wake and report the disabled policy. `batc-update enable` resumes it.

`batc-update rollback` switches to the previous verified managed version and
disables automatic updates until explicitly re-enabled. The initial enrollment
does not manufacture a previous managed version; keep the original deployment's
separate backups. Updates and rollback change future launches. Existing MCP,
central and BAT processes continue running; reconnect clients normally to load
the new package. Desktop bundles retain their own updater and lifecycle checks.

Status reports `next_launch`, installed source/version, policy, pending intent and
last outcome. A changed or uncertain pointer is not reported as a known installed
version. Configuration, credentials, user checkouts and operation journals are
outside the package switch. See the [contract](design/connector-updates.md).

## 發布與使用邊界

已發布的 Connector 0.2.7 預覽版加入 Linux 使用者層級自動更新。
由管理者先安裝含 `batc-update` 的已發布套件，保留部署收據，再用上方指令
選擇 `preview` 或 `stable` 並啟用排程。未知或被自行修改的安裝不會被接管。

每小時自動檢查，最多錯開 15 分鐘。無人登入的主機需確認使用者 systemd
能在登出後繼續執行。`check` 只查版本，`run` 依既有政策立即更新，`disable`
停用後續安裝，`enable` 恢復。`rollback` 回到前一份已驗證的自動更新版本，
並停用更新，避免立刻再升級。

更新供**下一次啟動**使用；正在執行的 MCP、central 與 BAT 工作不會被重啟。
收據分開記錄安裝版本、待核對狀態及最後結果。正常重新連線後載入新套件；
桌面內附 runtime 仍遵循原本的安全停止與簽章規則。安裝成功不是實機驗收。

## Publisher checklist

Use a new three-part Connector version for every distributed code change. Build
from a fixed published tag, pass its required CI and package fixture, then add
this section to that release's existing `release-manifest.json`:

```json
"connector_update": {
  "protocol": 1,
  "compatibility_epoch": 1,
  "python": "3.13",
  "canonical_skill_sha256": "<SHA-256 of skills/bat-agent-connector/SKILL.md at the release commit>"
}
```

The manifest retains format 1, the fixed repository URL, release tag,
`source_commit`, `versions.connector`, artifact digests and
`validation.status: "passed"`. Upload it as a release asset with GitHub's SHA-256
digest. Release assets and the manifest are immutable for a package version;
correct a bad release with a new package version. An incompatible data/launcher
change needs a new compatibility epoch and a reviewed migration, not automatic
application through epoch 1. Existing releases without this section are ignored.

Run the real package fixture on disk:

```bash
uv run python scripts/validate_connector_update.py --receipt "$HOME/agent-work/tmp/update-proof/receipt.json"
```

The fixture installs two local published-tag substitutes using real locked
packages, verifies all package files and canonical skills, and exercises actual
enrollment, upgrade, launchers and rollback. An already loaded interpreter keeps
its original identity while a new launch gets the new version; a journal sentinel
stays unchanged. A repeated stage uses the verified installation without fetching.
It does not contact BAT, run a model, or modify an existing installation.
