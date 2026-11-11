use super::*;

/// Pure replay result for one guarded capture. It grants no runtime effect
/// authorization. All four baseline entries must be Owned before native apply.
pub struct Resolution {
    captured_login: LoginIdentity,
    pub(super) owner: OwnerEvidence,
    pub(super) paths: [PathBuf; 4],
    pub(super) observed: [ObservedSlot; 4],
    pub(super) allowed: [Vec<Option<String>>; 4],
    pub(super) head: String,
    pub(super) terminal: Option<Terminal>,
    pub(super) settled: bool,
    pub(super) effect_session: Option<u32>,
    effect_reference: Option<FrozenPlanRef>,
}
impl Resolution {
    pub fn ownership(
        &self,
        owner: &OwnerEvidence,
        artifact: Artifact,
        path: &Path,
        current: Option<&[u8]>,
    ) -> FileOwnership {
        let i = index(artifact);
        if owner != &self.owner || path != self.paths[i] {
            return FileOwnership::Unknown;
        }
        let observed = &self.observed[i];
        if observed.bytes() != current || matches!(observed, ObservedSlot::Unavailable) {
            return FileOwnership::Unknown;
        }
        if matches!(observed, ObservedSlot::External(_)) {
            return FileOwnership::ReferenceOnly;
        }
        if observed
            .version()
            .is_some_and(|v| self.allowed[i].contains(&v))
        {
            FileOwnership::Owned {
                owner: owner.clone(),
                artifact,
                sha256: digest(current.unwrap_or_default()),
            }
        } else {
            FileOwnership::Unknown
        }
    }
    pub fn baseline(&self) -> Result<[Baseline; 4]> {
        let mut result = Vec::new();
        for a in ARTIFACTS {
            let i = index(a);
            ensure(matches!(
                self.ownership(&self.owner, a, &self.paths[i], self.observed[i].bytes()),
                FileOwnership::Owned { .. }
            ))?;
            result.push(Baseline {
                artifact: a,
                sha256: self.observed[i].version().ok_or(INVALID)?,
            });
        }
        result.try_into().map_err(|_| INVALID)
    }
    pub fn head(&self) -> &str {
        &self.head
    }
    pub fn terminal(&self) -> Option<&Terminal> {
        self.terminal.as_ref()
    }
    pub fn settled(&self) -> bool {
        self.settled
    }
    /// This is only the historical-session gate. Native review/lifecycle/expiry
    /// authorization and a fresh guarded capture are still required per effect.
    pub fn require_effect_session(
        &self,
        login: &LoginIdentity,
        frozen: &FrozenPlanRef,
    ) -> Result<()> {
        ensure(
            login.valid()
                && login == &self.captured_login
                && login.owner_sid == self.owner.owner_sid
                && self.effect_session == Some(login.session_id)
                && self.effect_reference.as_ref() == Some(frozen)
                && !self.settled,
        )
    }
}

use crate::connection_publish::ArtifactState;
use std::collections::BTreeSet;

type Versions = [Vec<Option<String>>; 4];
fn single_baseline(versions: &Versions) -> Result<[Baseline; 4]> {
    ensure(versions.iter().all(|v| v.len() == 1))?;
    Ok(std::array::from_fn(|i| Baseline {
        artifact: ARTIFACTS[i],
        sha256: versions[i][0].clone(),
    }))
}
fn history<'a>(
    link: &PublicationLink,
    histories: &'a [PublicationHistory],
) -> Result<&'a PublicationHistory> {
    let mut matching = histories
        .iter()
        .filter(|h| h.frozen.reference == link.frozen);
    let h = matching.next().ok_or(INVALID)?;
    ensure(matching.next().is_none())?;
    Ok(h)
}
fn validate_plan(
    c: &CapturedEvidence,
    link: &PublicationLink,
    h: &PublicationHistory,
) -> Result<()> {
    let plan = &h.frozen.plan;
    ensure(
        h.pointer == c.receipt.scope.pointer
            && plan.root == c.receipt.scope.kit_root
            && crate::connection_setup::frozen::owner(plan) == &c.owner
            && plan.generator_version == link.generator_version,
    )?;
    for (i, a) in ARTIFACTS.into_iter().enumerate() {
        if let Some(step) = plan.steps.iter().find(|s| s.artifact == a) {
            ensure(
                step.before_digest == link.baseline[i].sha256
                    && step.after_digest == plan.output(a).sha256
                    && step.owner == c.owner,
            )?;
        } else {
            ensure(link.baseline[i].sha256.as_deref() == Some(plan.output(a).sha256.as_str()))?;
        }
    }
    // The original reader validates each receipt against its matching step. Only
    // the last reached step can lag as Intended in a sequential publication.
    ensure(h.reached.iter().enumerate().all(|(i, (a, published))| {
        plan.steps.get(i).is_some_and(|s| s.artifact == *a)
            && (*published || i + 1 == h.reached.len())
    }))
}
fn apply_publication(
    c: &CapturedEvidence,
    link: &PublicationLink,
    hash: &str,
    h: &PublicationHistory,
    versions: &mut Versions,
    used: &mut BTreeSet<EvidenceKey>,
) -> Result<Option<Terminal>> {
    validate_plan(c, link, h)?;
    let closure_key = EvidenceKey::Closure(hash.into());
    if let Some(stored) = c.records.get(&closure_key) {
        let Record::Closure(close) = &stored.record else {
            return Err(INVALID);
        };
        ensure(
            h.journal_sha256.is_none()
                && close.baseline == link.baseline
                && close.link_sha256 == hash
                && close.journal_absent,
        )?;
        if close.reason == CloseReason::Unchanged {
            ensure(
                h.frozen.plan.steps.is_empty()
                    && close.decision_reference == link.acceptance_reference,
            )?;
        }
        used.insert(closure_key);
        return Ok(Some(Terminal {
            journal_sha256: None,
            decision_sha256: None,
            closure_sha256: Some(digest(&stored.bytes)),
            migration_sha256: None,
        }));
    }
    for step in &h.frozen.plan.steps {
        let slot = &mut versions[index(step.artifact)];
        match h.reached(step.artifact) {
            Some(true) => *slot = vec![Some(step.after_digest.clone())],
            Some(false) => slot.push(Some(step.after_digest.clone())),
            None => (),
        }
    }
    if let Some((hash, observations)) = &h.decision {
        ensure(!h.complete && h.journal_sha256.is_some())?;
        for observation in observations {
            let slot = &mut versions[index(observation.artifact)];
            match observation.state {
                ArtifactState::ThirdBytes | ArtifactState::Unreadable => slot.clear(),
                _ => {
                    // Raw recovery observations do not confer provenance. An
                    // unreached after digest or deleted Published slot taints it.
                    if slot.contains(&observation.sha256) {
                        *slot = vec![observation.sha256.clone()];
                    } else {
                        slot.clear();
                    }
                }
            }
        }
        return Ok(Some(Terminal {
            journal_sha256: h.journal_sha256.clone(),
            decision_sha256: Some(hash.clone()),
            closure_sha256: None,
            migration_sha256: None,
        }));
    }
    if h.complete {
        ensure(h.journal_sha256.is_some())?;
        return Ok(Some(Terminal {
            journal_sha256: h.journal_sha256.clone(),
            decision_sha256: None,
            closure_sha256: None,
            migration_sha256: None,
        }));
    }
    Ok(None)
}
fn pointer_payload(bytes: &[u8], root: &Path, backend: crate::discovery::Backend) -> Result<()> {
    crate::installation::validate_pointer(bytes, root, backend).map_err(|_| INVALID)
}
fn migration_history<'a>(
    c: &CapturedEvidence,
    id: &str,
    startup: &Path,
    migrations: &'a [crate::migration::MigrationHistory],
) -> Result<&'a crate::migration::MigrationHistory> {
    let mut matching = migrations.iter().filter(|s| s.id() == id);
    let view = matching.next().ok_or(INVALID)?;
    ensure(
        matching.next().is_none()
            && view.config_path() == c.receipt.scope.pointer
            && view.startup_path() == startup,
    )?;
    Ok(view)
}
fn validate_saved_intent(
    c: &CapturedEvidence,
    intent: &MigrationIntent,
    saved: &crate::migration::SavedHistory,
) -> Result<()> {
    ensure(
        saved.config_path() == c.receipt.scope.pointer
            && saved.startup_path() == intent.startup_path
            && saved.login()
                == (LoginIdentity {
                    owner_sid: intent.binding.owner_sid.clone(),
                    session_id: intent.binding.session_id,
                })
            && saved.review_fingerprint() == intent.review_fingerprint
            && saved.restores() == intent.restores.as_deref()
            && digest(&saved.config(false)?) == intent.before_sha256,
    )?;
    pointer_payload(
        &saved.config(false)?,
        &c.receipt.scope.kit_root,
        saved.from(),
    )?;
    pointer_payload(&saved.config(true)?, &c.receipt.scope.kit_root, saved.to())
}
fn validate_termination(
    c: &CapturedEvidence,
    intent: &MigrationIntent,
    termination: &MigrationTermination,
    migrations: &[crate::migration::MigrationHistory],
    replaying: bool,
) -> Result<()> {
    ensure(termination.binding == intent.binding)?;
    let observed = migration_history(c, &intent.migration_id, &intent.startup_path, migrations)?;
    match &termination.outcome {
        // Only these Store changes are confined to Installation on replay:
        // Saved appearing after Refused, or the source/restore hash or phase
        // changing. New outcomes must still seal the exact admissible state.
        // Missing required Saved/views, invalid readers, scope/binding, intent
        // contents and restore lineage/SID/bytes remain resolution conflicts.
        MigrationOutcome::Refused => ensure(replaying || observed.saved().is_none()),
        MigrationOutcome::Abandoned | MigrationOutcome::Restored { .. } => {
            let saved = observed.saved().ok_or(INVALID)?;
            ensure(
                replaying
                    || (termination.saved_sha256.as_deref() == Some(saved.sha256())
                        && saved.phase() != crate::migration::Phase::Complete),
            )?;
            validate_saved_intent(c, intent, saved)?;
            if let MigrationOutcome::Restored {
                migration_id,
                saved_sha256,
            } = &termination.outcome
            {
                ensure(migration_id != &intent.migration_id)?;
                let restore = migration_history(c, migration_id, &intent.startup_path, migrations)?
                    .saved()
                    .ok_or(INVALID)?;
                ensure(
                    replaying
                        || (restore.sha256() == saved_sha256
                            && restore.phase() == crate::migration::Phase::Complete),
                )?;
                ensure(
                    restore.login().owner_sid == intent.binding.owner_sid
                        && restore.restores() == Some(intent.migration_id.as_str())
                        && restore.config(true)? == saved.config(false)?
                        && (restore.config(false)? == saved.config(true)?
                            || restore.config(false)? == saved.config(false)?),
                )?;
                pointer_payload(
                    &restore.config(false)?,
                    &c.receipt.scope.kit_root,
                    restore.from(),
                )?;
                pointer_payload(
                    &restore.config(true)?,
                    &c.receipt.scope.kit_root,
                    restore.to(),
                )?;
                ensure(restore.to() == saved.from())?;
            }
            Ok(())
        }
    }
}
fn apply_migration(
    c: &CapturedEvidence,
    intent: &MigrationIntent,
    hash: &str,
    migrations: &[crate::migration::MigrationHistory],
    versions: &mut Versions,
    used: &mut BTreeSet<EvidenceKey>,
) -> Result<Option<Terminal>> {
    let slot = &mut versions[index(Artifact::Installation)];
    ensure(slot.as_slice() == [Some(intent.before_sha256.clone())])?;
    let key = EvidenceKey::MigrationCompletion(hash.into());
    let Some(stored) = c.records.get(&key) else {
        // Pending effects cannot confer the proposed pointer. Preserve the
        // independently rooted evidence for the other three artifacts.
        slot.clear();
        return Ok(None);
    };
    if let Record::MigrationTermination(termination) = &stored.record {
        validate_termination(c, intent, termination, migrations, true)?;
        slot.clear();
        used.insert(key);
        return Ok(Some(Terminal {
            journal_sha256: None,
            decision_sha256: None,
            closure_sha256: None,
            migration_sha256: Some(digest(&stored.bytes)),
        }));
    }
    let Record::MigrationCompletion(completion) = &stored.record else {
        return Err(INVALID);
    };
    ensure(completion.binding == intent.binding)?;
    let mut matching = migrations.iter().filter(|s| s.id() == intent.migration_id);
    let saved = matching.next().ok_or(INVALID)?.saved().ok_or(INVALID)?;
    ensure(
        matching.next().is_none()
            && saved.sha256() == completion.saved_sha256
            && saved.phase() == crate::migration::Phase::Complete
            && saved.config_path() == c.receipt.scope.pointer
            && saved.startup_path() == intent.startup_path
            && saved.login()
                == (LoginIdentity {
                    owner_sid: intent.binding.owner_sid.clone(),
                    session_id: intent.binding.session_id,
                })
            && saved.review_fingerprint() == intent.review_fingerprint
            && saved.restores() == intent.restores.as_deref()
            && saved.from() != saved.to(),
    )?;
    let before = saved.config(false)?;
    let after = saved.config(true)?;
    ensure(digest(&before) == intent.before_sha256 && digest(&after) == completion.after_sha256)?;
    pointer_payload(&before, &c.receipt.scope.kit_root, saved.from())?;
    pointer_payload(&after, &c.receipt.scope.kit_root, saved.to())?;
    if let Some(source) = &intent.restores {
        // A restore must name an earlier independently bound migration, whose
        // exact original/next pair is reversed by this new authorized edge.
        let prior = c
            .records
            .values()
            .find_map(|r| match &r.record {
                Record::MigrationIntent(i)
                    if &i.migration_id == source && used.contains(&r.record.key()) =>
                {
                    Some(i)
                }
                _ => None,
            })
            .ok_or(INVALID)?;
        let prior_saved = migrations
            .iter()
            .find(|s| s.id() == prior.migration_id)
            .and_then(|s| s.saved())
            .ok_or(INVALID)?;
        ensure(prior_saved.config(true)? == before && prior_saved.config(false)? == after)?;
    }
    *slot = vec![Some(completion.after_sha256.clone())];
    used.insert(key);
    Ok(Some(Terminal {
        journal_sha256: None,
        decision_sha256: None,
        closure_sha256: None,
        migration_sha256: Some(digest(&stored.bytes)),
    }))
}
pub(super) fn resolve(
    c: &CapturedEvidence,
    publications: &[PublicationHistory],
    migrations: &[crate::migration::MigrationHistory],
) -> Result<Resolution> {
    ensure(publications.len() <= HISTORY_LIMIT && migrations.len() <= HISTORY_LIMIT)?;
    let mut migration_ids = BTreeSet::new();
    ensure(
        migrations
            .iter()
            .all(|view| migration_ids.insert(view.id())),
    )?;
    let mut intent_ids = BTreeSet::new();
    ensure(c.records.values().all(|stored| match &stored.record {
        Record::MigrationIntent(intent) => intent_ids.insert(&intent.migration_id),
        _ => true,
    }))?;
    let mut directory_identities = BTreeMap::new();
    let mut bind_directory = |path: &Path, identity: &ObjectIdentity| -> Result<()> {
        if let Some(previous) = directory_identities.insert(path.to_path_buf(), identity.clone()) {
            ensure(previous == *identity)?;
        }
        Ok(())
    };
    bind_directory(&c.receipt.scope.state_root, &c.receipt.scope.state_identity)?;
    for slot in &c.receipt.slots {
        bind_directory(&slot.nearest_existing_parent, &slot.parent_identity)?;
    }
    for directory in &c.receipt.directories {
        if let DirectoryReservation::ProvenExisting {
            canonical_path,
            identity,
            ..
        } = directory
        {
            bind_directory(canonical_path, identity)?;
        }
    }
    for stored in c.records.values() {
        if let Record::Directory(record) = &stored.record {
            bind_directory(&record.canonical_path, &record.identity)?;
        }
    }
    let reviews: Vec<_> = c
        .records
        .values()
        .filter_map(|s| match &s.record {
            Record::Publication(link) => Some(link.frozen.review_digest.as_str()),
            _ => None,
        })
        .collect();
    let mut used = BTreeSet::from([EvidenceKey::Provisioning]);
    for (key, stored) in &c.records {
        match &stored.record {
            Record::Directory(r) => {
                ensure(
                    r.canonical_path
                        .parent()
                        .and_then(|p| directory_identities.get(p))
                        == Some(&r.parent_identity),
                )?;
                ensure(super::directory_reserved(
                    &c.receipt,
                    &r.canonical_path,
                    &reviews,
                ))?;
                used.insert(key.clone());
            }
            Record::Managed(r) => {
                ensure(
                    c.receipt
                        .managed_connector
                        .as_ref()
                        .is_none_or(|m| m == &r.managed),
                )?;
                used.insert(key.clone());
            }
            _ => (),
        }
    }
    let mut head = c.receipt_sha256.clone();
    let mut terminal = None;
    let mut versions: Versions = std::array::from_fn(|_| vec![None]);
    let mut settled = true;
    let mut effect_session = None;
    let mut effect_reference = None;
    for count in 0..=HISTORY_LIMIT {
        let key = EvidenceKey::Lineage(head.clone());
        let Some(stored) = c.records.get(&key) else {
            break;
        };
        ensure(count < HISTORY_LIMIT && settled && used.insert(key.clone()))?;
        let hash = digest(&stored.bytes);
        match &stored.record {
            Record::Publication(link) => {
                ensure(
                    link.predecessor_terminal == terminal
                        && link.baseline == single_baseline(&versions)?,
                )?;
                let h = history(link, publications)?;
                terminal = apply_publication(c, link, &hash, h, &mut versions, &mut used)?;
                settled = terminal.is_some();
                effect_session = (!settled).then_some(link.binding.session_id);
                effect_reference = (!settled).then(|| link.frozen.clone());
            }
            Record::MigrationIntent(intent) => {
                ensure(terminal.as_ref() == Some(&intent.predecessor_terminal))?;
                terminal = apply_migration(c, intent, &hash, migrations, &mut versions, &mut used)?;
                settled = terminal.is_some();
                effect_session = None;
                effect_reference = None;
            }
            _ => return Err(INVALID),
        }
        head = hash;
    }
    // Forks, cycles, orphan closures/completions and unrelated authority records
    // are conflicts. Exported previews outside this namespace are not authority.
    ensure(used.len() == c.records.len())?;
    Ok(Resolution {
        captured_login: c.login.clone(),
        owner: c.owner.clone(),
        paths: std::array::from_fn(|i| c.receipt.slots[i].canonical_path.clone()),
        observed: c.observed.clone(),
        allowed: versions,
        head,
        terminal,
        settled,
        effect_session,
        effect_reference,
    })
}
fn candidate(c: &CapturedEvidence, record: &Record) -> Result<CapturedEvidence> {
    let mut records: BTreeMap<_, _> = c
        .records
        .iter()
        .map(|(k, s)| {
            (
                k.clone(),
                Stored {
                    record: s.record.clone(),
                    bytes: s.bytes.clone(),
                },
            )
        })
        .collect();
    let bytes = record.encode()?;
    ensure(
        records
            .insert(
                record.key(),
                Stored {
                    record: record.clone(),
                    bytes,
                },
            )
            .is_none(),
    )?;
    Ok(CapturedEvidence {
        receipt: c.receipt.clone(),
        receipt_sha256: c.receipt_sha256.clone(),
        owner: c.owner.clone(),
        login: c.login.clone(),
        records,
        observed: c.observed.clone(),
    })
}
pub(super) fn validate_append(
    c: &CapturedEvidence,
    record: &Record,
    publications: &[PublicationHistory],
    migrations: &[crate::migration::MigrationHistory],
) -> Result<()> {
    if let Some(existing) = c.records.get(&record.key()) {
        ensure(existing.record == *record)?;
        if matches!(record, Record::MigrationIntent(_))
            && !c
                .records
                .contains_key(&EvidenceKey::MigrationCompletion(digest(&existing.bytes)))
        {
            // A lost create-only reply before migration begin can re-read the
            // original intent. The predecessor must still be exactly current.
            let prefix = CapturedEvidence {
                receipt: c.receipt.clone(),
                receipt_sha256: c.receipt_sha256.clone(),
                owner: c.owner.clone(),
                login: c.login.clone(),
                observed: c.observed.clone(),
                records: c
                    .records
                    .iter()
                    .filter(|(key, _)| **key != record.key())
                    .map(|(key, stored)| {
                        (
                            key.clone(),
                            Stored {
                                record: stored.record.clone(),
                                bytes: stored.bytes.clone(),
                            },
                        )
                    })
                    .collect(),
            };
            return validate_append(&prefix, record, publications, migrations);
        }
        resolve(c, publications, migrations)?;
        return Ok(());
    }
    if matches!(record, Record::MigrationCompletion(_)) {
        resolve(&candidate(c, record)?, publications, migrations)?.baseline()?;
        return Ok(());
    }
    if let Record::MigrationTermination(termination) = record {
        let intent = c
            .records
            .values()
            .find(|s| digest(&s.bytes) == termination.intent_sha256)
            .ok_or(INVALID)?;
        let Record::MigrationIntent(intent) = &intent.record else {
            return Err(INVALID);
        };
        validate_termination(c, intent, termination, migrations, false)?;
        resolve(&candidate(c, record)?, publications, migrations)?;
        return Ok(());
    }
    let current = resolve(c, publications, migrations)?;
    match record {
        Record::Publication(link) => {
            ensure(
                current.settled
                    && current.head == link.predecessor_sha256
                    && current.terminal == link.predecessor_terminal
                    && current.baseline()? == link.baseline,
            )?;
            let h = history(link, publications)?;
            ensure(h.journal_sha256.is_none() && h.decision.is_none())?;
            resolve(&candidate(c, record)?, publications, migrations)?.baseline()?;
        }
        Record::Closure(close) => {
            ensure(
                !current.settled
                    && current.head == close.link_sha256
                    && current.baseline()? == close.baseline,
            )?;
            resolve(&candidate(c, record)?, publications, migrations)?.baseline()?;
        }
        Record::MigrationIntent(intent) => {
            ensure(
                current.settled
                    && current.head == intent.predecessor_sha256
                    && current.terminal.as_ref() == Some(&intent.predecessor_terminal)
                    && current.baseline()?[index(Artifact::Installation)]
                        .sha256
                        .as_deref()
                        == Some(intent.before_sha256.as_str()),
            )?;
            ensure(!c.records.values().any(|s| {
                matches!(&s.record, Record::MigrationIntent(i)
                    if i.migration_id == intent.migration_id)
            }))?;
        }
        Record::Directory(_) | Record::Managed(_) => {
            resolve(&candidate(c, record)?, publications, migrations)?;
        }
        _ => return Err(INVALID),
    }
    Ok(())
}
