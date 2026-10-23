//! Durable local backend migration. No shell/process effects: trusted adapters provide them.
use crate::{
    discovery::{Backend, MonitorIdentity, Ownership},
    migration_io as io,
    ownership::epoch_valid,
    process_adapter::{LoginIdentity, ProcessSnapshot},
    strict_json, Result,
};
use base64::{engine::general_purpose::STANDARD, Engine};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Clone, PartialEq, Eq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Owner {
    backend: Backend,
    pid: u32,
    birth: u64,
    executable: String,
    arguments: Vec<String>,
    sid: String,
    session: u32,
    epoch: String,
}
impl Owner {
    pub fn from_monitor(m: &MonitorIdentity, login: &LoginIdentity) -> Result<Self> {
        if m.ownership != Ownership::CurrentLogin || &m.process.login != login {
            return Err("OTHER_LOGIN_OWNER");
        }
        if m.legacy || !m.process.valid() || m.instance.as_deref().is_none_or(|v| !epoch_valid(v)) {
            return Err("OWNER_UNPROVEN");
        }
        Ok(Self {
            backend: m.backend,
            pid: m.process.pid,
            birth: m.process.created_filetime,
            executable: m.process.executable.clone(),
            arguments: m.process.arguments.clone(),
            sid: login.owner_sid.clone(),
            session: login.session_id,
            epoch: m.instance.clone().unwrap(),
        })
    }
    fn valid(&self) -> bool {
        epoch_valid(&self.epoch)
            && ProcessSnapshot {
                pid: self.pid,
                created_filetime: self.birth,
                executable: self.executable.clone(),
                arguments: self.arguments.clone(),
                login: LoginIdentity {
                    owner_sid: self.sid.clone(),
                    session_id: self.session,
                },
            }
            .valid()
    }
    pub fn matches(&self, m: &MonitorIdentity, login: &LoginIdentity) -> bool {
        Self::from_monitor(m, login).as_ref() == Ok(self)
    }
    pub fn epoch(&self) -> &str {
        &self.epoch
    }
}
/// The caller owns native source/installation identity, never WebView paths or command text.
/// Guards must remain held on their owning thread. Absence must be positively proven.
pub trait Platform {
    type LauncherGuard;
    type MonitorGuard;
    fn launcher_guard(&mut self) -> Result<Self::LauncherGuard>;
    fn monitor_guard(&mut self) -> Result<Self::MonitorGuard>;
    fn login(&self) -> Result<LoginIdentity>;
    fn discover(&mut self) -> Result<Option<MonitorIdentity>>;
    fn request_quit(&mut self, expected: &Owner) -> Result<()>;
    /// Normal bounded launch. An error may be uncertain; the core never automatically retries it.
    fn launch(&mut self, backend: Backend) -> Result<()>;
    /// Validate both exact config payloads against the SAME trusted installation/source.
    fn validate_config(&self, bytes: &[u8], backend: Backend) -> Result<()>;
    /// Shortcut inspection/rendering is native-only, with fixed expected target/argv/working dir.
    fn classify_shortcut(&self, bytes: &[u8]) -> Result<Backend>;
    fn shortcut(&self, backend: Backend) -> Result<Vec<u8>>;
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Prepared,
    QuitRequested,
    Stopped,
    StartupWritten,
    ConfigWritten,
    LaunchRequested,
    Complete,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Progress {
    WaitingExit,
    WaitingLaunch,
    Complete,
}
/// Historical local intent summary; contains no paths, process identity or original file bytes.
#[derive(Serialize)]
pub struct Status {
    pub id: String,
    pub phase: Phase,
    pub from: Backend,
    pub to: Backend,
    pub autostart_entry_present: bool,
    pub review_fingerprint: String,
    pub restores: Option<String>,
}
#[derive(Clone, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u8,
    id: String,
    config_path: PathBuf,
    startup_path: PathBuf,
    sid: String,
    session: u32,
    from: Backend,
    to: Backend,
    review_fingerprint: String,
    original_config: String,
    next_config: String,
    original_startup: Option<String>,
    next_startup: Option<String>,
    owner: Option<Owner>,
    phase: Phase,
    accepted_owner: Option<Owner>,
    restores: Option<String>,
}
impl Saved {
    fn encode(&self) -> Result<Vec<u8>> {
        serde_json::to_vec(self).map_err(|_| "MIGRATION_INVALID")
    }
    fn config(&self, next: bool) -> Result<Vec<u8>> {
        decode(if next {
            &self.next_config
        } else {
            &self.original_config
        })
    }
    fn startup(&self, next: bool) -> Result<Option<Vec<u8>>> {
        (if next {
            &self.next_startup
        } else {
            &self.original_startup
        })
        .as_deref()
        .map(decode)
        .transpose()
    }
}
fn decode(value: &str) -> Result<Vec<u8>> {
    if value.len() > io::MAX_SLOT.div_ceil(3) * 4 {
        return Err("MIGRATION_INVALID");
    }
    let bytes = STANDARD.decode(value).map_err(|_| "MIGRATION_INVALID")?;
    if bytes.len() > io::MAX_SLOT {
        return Err("MIGRATION_INVALID");
    }
    Ok(bytes)
}
fn id_valid(id: &str) -> bool {
    epoch_valid(id)
}
/// Opaque native review snapshot. Only the fingerprint and safe summary may reach UI.
pub struct Preview {
    from: Backend,
    config: Vec<u8>,
    startup: Option<Vec<u8>>,
    owner: Option<Owner>,
    sid: String,
    session: u32,
    fingerprint: String,
}
impl Preview {
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }
    pub fn backend(&self) -> Backend {
        self.from
    }
    pub fn autostart_entry_present(&self) -> bool {
        self.startup.is_some()
    }
}
struct Proposal<'a> {
    id: &'a str,
    to: Backend,
    config: &'a [u8],
    startup: Option<Vec<u8>>,
    restores: Option<String>,
}
/// Fixed native paths: config is fleet.json; only the named Startup slot may change.
pub struct Store {
    config: PathBuf,
    startup: PathBuf,
    directory: PathBuf,
}
/// Immutable history validated by Store's original bounded Saved reader. This
/// record proves no creation authority; consumers must separately anchor it.
pub struct SavedHistory {
    saved: Saved,
    sha256: String,
}
/// Guarded observation of one fixed migration id. None is a genuine missing
/// Saved file, never a parse/permission error. Re-read across effects or guards.
pub struct MigrationHistory {
    id: String,
    config_path: PathBuf,
    startup_path: PathBuf,
    saved: Option<SavedHistory>,
}
impl MigrationHistory {
    pub fn id(&self) -> &str {
        &self.id
    }
    pub fn config_path(&self) -> &Path {
        &self.config_path
    }
    pub fn startup_path(&self) -> &Path {
        &self.startup_path
    }
    pub fn saved(&self) -> Option<&SavedHistory> {
        self.saved.as_ref()
    }
}
impl SavedHistory {
    pub fn sha256(&self) -> &str {
        &self.sha256
    }
    pub fn id(&self) -> &str {
        &self.saved.id
    }
    pub fn config_path(&self) -> &Path {
        &self.saved.config_path
    }
    pub fn startup_path(&self) -> &Path {
        &self.saved.startup_path
    }
    pub fn login(&self) -> LoginIdentity {
        LoginIdentity {
            owner_sid: self.saved.sid.clone(),
            session_id: self.saved.session,
        }
    }
    pub fn phase(&self) -> Phase {
        self.saved.phase
    }
    pub fn review_fingerprint(&self) -> &str {
        &self.saved.review_fingerprint
    }
    pub fn restores(&self) -> Option<&str> {
        self.saved.restores.as_deref()
    }
    pub fn from(&self) -> Backend {
        self.saved.from
    }
    pub fn to(&self) -> Backend {
        self.saved.to
    }
    pub fn config(&self, next: bool) -> Result<Vec<u8>> {
        self.saved.config(next)
    }
}
impl Store {
    /// Reads at most MAX_JOURNAL (6,000,000 bytes), with the original strict
    /// validator and 1 MiB decoded-slot bounds. No begin, advance or writes.
    pub fn read_history(&self, id: &str) -> Result<SavedHistory> {
        let (saved, bytes) = self.read(id)?;
        Ok(SavedHistory {
            saved,
            sha256: crate::digest(&bytes),
        })
    }
    /// Read-only absence or the original bounded Saved validator. The caller
    /// proves private storage provenance and holds its guards. No begin/advance.
    pub fn observe_history(&self, id: &str) -> Result<MigrationHistory> {
        let saved = match self.read_history(id) {
            Ok(saved) => Some(saved),
            Err("MIGRATION_NOT_FOUND") => None,
            Err(error) => return Err(error),
        };
        Ok(MigrationHistory {
            id: id.into(),
            config_path: self.config.clone(),
            startup_path: self.startup.clone(),
            saved,
        })
    }
    pub fn new(config: &Path, startup_directory: &Path) -> Result<Self> {
        if !config.is_absolute()
            || config.file_name().is_none_or(|n| n != "fleet.json")
            || !startup_directory.is_absolute()
        {
            return Err("MIGRATION_INVALID_PATH");
        }
        Ok(Self {
            config: config.into(),
            startup: startup_directory.join("Open BAT.lnk"),
            directory: config.parent().unwrap().join("fleet-migrations"),
        })
    }
    fn path(&self, id: &str) -> Result<PathBuf> {
        io::directory(&self.directory)?;
        if !id_valid(id) {
            return Err("INVALID_REQUEST");
        }
        Ok(self.directory.join(format!("{id}.json")))
    }
    fn pointer(&self) -> PathBuf {
        self.directory.join("active.json")
    }
    fn active(&self) -> Result<Option<String>> {
        io::directory(&self.directory)?;
        let Some(bytes) = io::read(&self.pointer(), 128)? else {
            return Ok(None);
        };
        let value: String = serde_json::from_value(strict_json::parse(&bytes, 128)?)
            .map_err(|_| "MIGRATION_INVALID")?;
        if !id_valid(&value) {
            return Err("MIGRATION_INVALID");
        }
        Ok(Some(value))
    }
    fn read(&self, id: &str) -> Result<(Saved, Vec<u8>)> {
        let bytes = io::read(&self.path(id)?, io::MAX_JOURNAL)?.ok_or("MIGRATION_NOT_FOUND")?;
        let saved: Saved = serde_json::from_value(strict_json::parse(&bytes, io::MAX_JOURNAL)?)
            .map_err(|_| "MIGRATION_INVALID")?;
        if saved.version != 1
            || saved.id != id
            || saved.config_path != self.config
            || saved.startup_path != self.startup
            || !(LoginIdentity {
                owner_sid: saved.sid.clone(),
                session_id: saved.session,
            })
            .valid()
            || saved
                .owner
                .as_ref()
                .is_some_and(|o| !o.valid() || o.sid != saved.sid || o.session != saved.session)
            || saved.accepted_owner.as_ref().is_some_and(|o| {
                !o.valid()
                    || o.sid != saved.sid
                    || o.session != saved.session
                    || o.backend != saved.to
            })
            || saved.restores.as_deref().is_some_and(|v| !id_valid(v))
            || (saved.phase == Phase::Complete) != saved.accepted_owner.is_some()
            || (saved.phase == Phase::QuitRequested && saved.owner.is_none())
            || saved
                .owner
                .as_ref()
                .is_some_and(|o| o.backend != saved.from)
            || saved.review_fingerprint.len() != 64
            || !saved
                .review_fingerprint
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err("MIGRATION_INVALID");
        }
        saved.config(false)?;
        saved.config(true)?;
        saved.startup(false)?;
        saved.startup(true)?;
        Ok((saved, bytes))
    }
    /// All launch entrypoints must consult this before starting a supervisor.
    pub fn pending(&self) -> Result<Option<(String, Phase)>> {
        let Some(id) = self.active()? else {
            return Ok(None);
        };
        let (s, _) = self.read(&id)?;
        Ok((s.phase != Phase::Complete).then_some((id, s.phase)))
    }
    pub fn status(&self, id: &str) -> Result<Status> {
        let (saved, _) = self.read(id)?;
        Ok(Status {
            id: saved.id,
            phase: saved.phase,
            from: saved.from,
            to: saved.to,
            autostart_entry_present: saved.next_startup.is_some(),
            review_fingerprint: saved.review_fingerprint,
            restores: saved.restores,
        })
    }
    fn slots(&self) -> Result<(Vec<u8>, Option<Vec<u8>>)> {
        io::directory(self.startup.parent().unwrap())?;
        Ok((
            io::read(&self.config, 16384)?.ok_or("INSTALLATION_UNAVAILABLE")?,
            io::read(&self.startup, io::MAX_SLOT)?,
        ))
    }
    fn validate(&self, s: &Saved, p: &impl Platform) -> Result<()> {
        let login = p.login()?;
        if login.owner_sid != s.sid || login.session_id != s.session {
            return Err("OTHER_LOGIN_OWNER");
        }
        p.validate_config(&s.config(false)?, s.from)?;
        p.validate_config(&s.config(true)?, s.to)?;
        for bytes in [s.startup(false)?, s.startup(true)?].iter().flatten() {
            p.classify_shortcut(bytes)?;
        }
        let (config, startup) = self.slots()?;
        if config != s.config(false)? && config != s.config(true)? {
            return Err("MIGRATION_CHANGED");
        }
        if startup != s.startup(false)? && startup != s.startup(true)? {
            return Err("MIGRATION_CHANGED");
        }
        Ok(())
    }
    fn observe(&self, p: &mut impl Platform, from: Backend) -> Result<Preview> {
        let (config, startup) = self.slots()?;
        let login = p.login()?;
        if !login.valid() {
            return Err("OWNER_UNPROVEN");
        }
        p.validate_config(&config, from)?;
        if let Some(bytes) = &startup {
            p.classify_shortcut(bytes)?;
        }
        let owner = p
            .discover()?
            .map(|m| Owner::from_monitor(&m, &login))
            .transpose()?;
        if owner.as_ref().is_some_and(|o| o.backend != from) {
            return Err("MIGRATION_BACKEND_CHANGED");
        }
        let fingerprint = crate::digest(
            &serde_json::to_vec(&serde_json::json!([
                "fleet-migration-preview-v1",
                self.config,
                self.startup,
                from,
                STANDARD.encode(&config),
                startup.as_deref().map(|b| STANDARD.encode(b)),
                owner,
                login.owner_sid,
                login.session_id
            ]))
            .map_err(|_| "MIGRATION_INVALID")?,
        );
        Ok(Preview {
            from,
            config,
            startup,
            owner,
            sid: login.owner_sid,
            session: login.session_id,
            fingerprint,
        })
    }
    pub fn preview(&self, p: &mut impl Platform, from: Backend) -> Result<Preview> {
        let _launcher = p.launcher_guard()?;
        self.observe(p, from)
    }
    fn activate_prepared(&self, p: &mut impl Platform, saved: &Saved) -> Result<()> {
        let active = self.active()?;
        if active.as_deref() == Some(&saved.id) {
            return Ok(());
        }
        if saved.phase != Phase::Prepared
            || self.observe(p, saved.from)?.fingerprint != saved.review_fingerprint
        {
            return Err("MIGRATION_CHANGED");
        }
        if let Some(old) = &active {
            if self.read(old)?.0.phase != Phase::Complete && saved.restores.as_deref() != Some(old)
            {
                return Err("MIGRATION_PENDING");
            }
        }
        let original = active.map(|v| serde_json::to_vec(&v).unwrap());
        io::replace(
            &self.pointer(),
            original.as_deref(),
            Some(&serde_json::to_vec(&saved.id).unwrap()),
            128,
        )
    }
    /// Adapter must compare the UI's reviewed fingerprint before passing this native snapshot.
    /// Reusing an ID replays only the same original snapshot and fixed request.
    pub fn begin(
        &self,
        p: &mut impl Platform,
        id: &str,
        expected: &Preview,
        to: Backend,
        next_config: &[u8],
        autostart: bool,
    ) -> Result<Phase> {
        let _launcher = p.launcher_guard()?;
        if let Ok((saved, _)) = self.read(id) {
            if saved.review_fingerprint != expected.fingerprint
                || saved.from != expected.from
                || saved.to != to
                || saved.config(true)? != next_config
                || saved.next_startup.is_some() != autostart
                || saved.config(false)? != expected.config
                || saved.startup(false)? != expected.startup
                || saved.owner != expected.owner
                || saved.sid != expected.sid
                || saved.session != expected.session
            {
                return Err("MIGRATION_ID_CONFLICT");
            }
            self.validate(&saved, p)?;
            self.activate_prepared(p, &saved)?;
            return Ok(saved.phase);
        }
        let startup = if autostart {
            Some(p.shortcut(to)?)
        } else {
            None
        };
        self.prepare(
            p,
            expected,
            Proposal {
                id,
                to,
                config: next_config,
                startup,
                restores: None,
            },
        )
    }
    fn prepare(
        &self,
        p: &mut impl Platform,
        expected: &Preview,
        next: Proposal<'_>,
    ) -> Result<Phase> {
        self.path(next.id)?;
        let old_active = self.active()?;
        if let Some(old) = &old_active {
            if self.read(old)?.0.phase != Phase::Complete && next.restores.as_deref() != Some(old) {
                return Err("MIGRATION_PENDING");
            }
        }
        if self.observe(p, expected.from)?.fingerprint != expected.fingerprint {
            return Err("MIGRATION_PREVIEW_CHANGED");
        }
        p.validate_config(next.config, next.to)?;
        if let Some(bytes) = &next.startup {
            if p.classify_shortcut(bytes)? != next.to {
                return Err("MIGRATION_INVALID");
            }
        }
        let saved = Saved {
            version: 1,
            id: next.id.into(),
            config_path: self.config.clone(),
            startup_path: self.startup.clone(),
            sid: expected.sid.clone(),
            session: expected.session,
            from: expected.from,
            to: next.to,
            review_fingerprint: expected.fingerprint.clone(),
            original_config: STANDARD.encode(&expected.config),
            next_config: STANDARD.encode(next.config),
            original_startup: expected.startup.as_deref().map(|b| STANDARD.encode(b)),
            next_startup: next.startup.as_deref().map(|b| STANDARD.encode(b)),
            owner: expected.owner.clone(),
            phase: Phase::Prepared,
            accepted_owner: None,
            restores: next.restores,
        };
        self.validate(&saved, p)?;
        std::fs::create_dir_all(&self.directory).map_err(|_| "MIGRATION_UNAVAILABLE")?;
        io::replace(
            &self.path(next.id)?,
            None,
            Some(&saved.encode()?),
            io::MAX_JOURNAL,
        )?;
        let expected = old_active.map(|v| serde_json::to_vec(&v).unwrap());
        io::replace(
            &self.pointer(),
            expected.as_deref(),
            Some(&serde_json::to_vec(next.id).unwrap()),
            128,
        )?;
        Ok(saved.phase)
    }
    /// Explicit reverse intent, using exactly the original bytes, never automatic rollback.
    pub fn restore(&self, p: &mut impl Platform, source: &str, id: &str) -> Result<Phase> {
        let _launcher = p.launcher_guard()?;
        if let Ok((saved, _)) = self.read(id) {
            if saved.restores.as_deref() != Some(source) {
                return Err("MIGRATION_ID_CONFLICT");
            }
            self.validate(&saved, p)?;
            self.activate_prepared(p, &saved)?;
            return Ok(saved.phase);
        }
        if self.active()?.as_deref() != Some(source) {
            return Err("MIGRATION_CHANGED");
        }
        let (old, _) = self.read(source)?;
        self.validate(&old, p)?;
        let (current, _) = self.slots()?;
        let from = if current == old.config(true)? {
            old.to
        } else {
            old.from
        };
        let expected = self.observe(p, from)?;
        self.prepare(
            p,
            &expected,
            Proposal {
                id,
                to: old.from,
                config: &old.config(false)?,
                startup: old.startup(false)?,
                restores: Some(source.into()),
            },
        )
    }
    fn save(&self, s: &Saved, expected: &mut Vec<u8>) -> Result<()> {
        let next = s.encode()?;
        io::replace(
            &self.path(&s.id)?,
            Some(expected),
            Some(&next),
            io::MAX_JOURNAL,
        )?;
        *expected = next;
        Ok(())
    }
    /// One bounded transition; callers poll WaitingExit/WaitingLaunch with their own finite deadline.
    pub fn advance(&self, p: &mut impl Platform, id: &str) -> Result<Progress> {
        let _launcher = p.launcher_guard()?;
        if self.active()?.as_deref() != Some(id) {
            return Err("MIGRATION_CHANGED");
        }
        let (mut s, mut raw) = self.read(id)?;
        self.validate(&s, p)?;
        if s.phase == Phase::Complete {
            return Ok(Progress::Complete);
        }
        let login = p.login()?;
        let current = p
            .discover()?
            .map(|m| Owner::from_monitor(&m, &login))
            .transpose()?;
        if s.phase == Phase::LaunchRequested {
            let Some(owner) = current else {
                return Err("MIGRATION_LAUNCH_UNKNOWN");
            };
            if owner.backend != s.to || s.owner.as_ref() == Some(&owner) {
                return Err("MIGRATION_OWNER_CHANGED");
            }
            if self.slots()? != (s.config(true)?, s.startup(true)?) {
                return Err("MIGRATION_CHANGED");
            }
            s.accepted_owner = Some(owner);
            s.phase = Phase::Complete;
            self.save(&s, &mut raw)?;
            return Ok(Progress::Complete);
        }
        if let Some(current) = current {
            if s.owner.as_ref() != Some(&current) {
                return Err("MIGRATION_OWNER_CHANGED");
            }
            if s.phase == Phase::Prepared {
                s.phase = Phase::QuitRequested;
                self.save(&s, &mut raw)?;
            }
            if s.phase == Phase::QuitRequested {
                p.request_quit(&current)?;
            }
            return Ok(Progress::WaitingExit);
        }
        let guard = p.monitor_guard()?;
        if p.discover()?.is_some() {
            return Err("MIGRATION_OWNER_CHANGED");
        }
        self.validate(&s, p)?;
        s.phase = Phase::Stopped;
        self.save(&s, &mut raw)?;
        let (_, startup) = self.slots()?;
        io::replace(
            &self.startup,
            startup.as_deref(),
            s.startup(true)?.as_deref(),
            io::MAX_SLOT,
        )?;
        s.phase = Phase::StartupWritten;
        self.save(&s, &mut raw)?;
        self.validate(&s, p)?;
        let (config, _) = self.slots()?;
        io::replace(&self.config, Some(&config), Some(&s.config(true)?), 16384)?;
        s.phase = Phase::ConfigWritten;
        self.save(&s, &mut raw)?;
        self.validate(&s, p)?;
        if p.discover()?.is_some() {
            return Err("MIGRATION_OWNER_CHANGED");
        }
        s.phase = Phase::LaunchRequested;
        self.save(&s, &mut raw)?;
        drop(guard);
        p.launch(s.to)?;
        Ok(Progress::WaitingLaunch)
    }
}
