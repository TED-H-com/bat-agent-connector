# Connector automatic updates / Connector 自動更新

Connector **0.2.15** includes automatic updates (introduced in 0.2.7). Start with the
[published stable release](https://github.com/TED-H-com/bat-agent-connector/releases/tag/v0.2.15)
and its matching source manifest and checksums.

This release adds background saved-conversation retention and explicit PM summaries
of fixed saved versions (#498), plus optional read-only host deployment observations
(#495). The enrolled Connector updater applies the new core to future launches;
it does not replace the desktop application. Desktop 0.1.11 uses the separate manual
package update described in the [installation guide](getting-started.md#update-an-existing-desktop).
The release's bundled-core fixtures build older cores from pinned published
0.2.12 and 0.2.14 sources before upgrading them; old desktop installer upgrades are separate.
exact source/CI and their validation scope are recorded in [delivery status](product/implementation-status.md).
Existing processes keep their loaded version until their ordinary restart.

The [verified operator installer](getting-started.md#2-install-connector)
produces `rollout-receipt.json` in its private version directory after verifying
the real installation. The v0.2.15 tag contains this installer; the plain `uv tool install` alternative does not
produce an enrollment receipt. On Linux retain this receipt and use its printed
absolute path below. `--enable-updates` runs enrollment and scheduling for you. Enrollment verifies
the receipt, installed source, canonical skill and the published release channel.
It refuses unknown/customized installations rather than replacing their files.

```bash
batc-update enable --channel stable --receipt /path/to/rollout-receipt.json
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

## Adopting an existing uv tool installation

Use a published tag containing the verified installer. Adoption checks
`~/.config/bat-agent-connector/install-receipt-connector.json` against the default
`~/.local/share/uv/tools/bat-agent-connector` environment, VCS metadata, Python
sources, packaged skill, generic skill and three owned entrypoint symlinks.
Customized, missing, foreign or already update-managed installations are refused.

```bash
python3 scripts/install_connector.py --tag <published-tag> --adopt-existing --enable-updates
```

The version may be the same or newer; an older version requires explicit
`--allow-downgrade`. The normal verified installation goes into a new managed root.
Checks precede replacing links and the generic skill; a failed swap restores
already replaced links. The old uv environment and receipt remain untouched.
`adoption-record.json` retains previous link targets, commit, version, skill digest
and timestamps; `adopted-SKILL.md` retains the previous skill. Without
`--enable-updates`, use the printed receipt enrollment commands.

Before enrollment, restore using the retained installer:

```bash
python3 scripts/install_connector.py --restore-adopted /path/to/managed-release-root
```

Every current entrypoint must still point directly at that root and the skill must
still match it. Restoration refuses changed links, including updater-managed links
after enrollment; use updater rollback for managed updates. Running processes and
application configuration remain unchanged.

既有 uv tool 安裝可用 `--adopt-existing` 驗證並接管；保留原環境與收據。
自行修改或已納入更新管理的安裝會被拒絕。啟用更新前可用
`--restore-adopted` 還原；啟用後請沿既有更新回退流程操作。

## 發布與使用邊界

Connector 自 0.2.7 起提供 Linux 使用者層級自動更新，目前穩定版為 0.2.15。
本版加入背景對話保存、明確請求的固定版本 PM 摘要（#498）及選填的主機部署唯讀觀察（#495）。
Connector 更新供下次啟動使用，不會替換桌面程式；Desktop 0.1.11 另依
[安裝指南](getting-started.zh-TW.md#更新既有桌面安裝)手動更新。
bundled-core fixture 先由 0.2.12 與 0.2.14 固定發布來源建置舊核心再升級；
舊桌面安裝器的完整升級另有範圍。最終來源／CI
見[交付狀態](product/implementation-status.md)；既有程序仍載入原版，直到正常重啟。
本版的驗證安裝器會產生 `rollout-receipt.json`。保留收據，再用上方指令
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

## Repairing an update-managed install

On v0.2.12 hosts after v0.2.14, the `batc-update` journal can report
`{"status": "failed", "code": "installed_source_mismatch"}` each hour. The wheel
added generated provenance at `bat_agent_connector/build-info.json`, which the
older updater compares against the source tree. v0.2.16+ puts wheel provenance
under `bat_agent_connector/skill/build-info.json`, excluded by the legacy source
comparison, so these hosts heal automatically on the next hourly timer once that
release is published on their channel.

Ordinary reinstall refuses an enrolled install with `already_update_managed`.
For explicit manual repair, download `install_connector.py` from the requested
release and use its actual tag in place of `vX`:

```sh
python3 install_connector.py --tag vX --repair-managed --dry-run
python3 install_connector.py --tag vX --repair-managed
```

Repair requires Linux, private valid updater state, and a uv tool Git installation
from this repository. The dry run validates state, installation provenance and
release metadata without writes. Activation uses the existing updater transaction;
the previous current release remains available for rollback. Pending updater
transactions must finish before repair. Existing sessions are not restarted.
