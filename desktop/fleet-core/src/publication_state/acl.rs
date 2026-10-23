use super::{Result, UNPROVEN};

pub(super) struct Entry {
    pub kind: u8,
    pub flags: u8,
    pub mask: u32,
    pub sid: String,
}
#[derive(Clone, Copy)]
pub(super) struct Policy<'a> {
    pub current: &'a str,
    pub trusted_installer: Option<&'a str>,
    pub private: bool,
    pub child_absent: bool,
}
pub(super) fn validate(
    owner: &str,
    protected: bool,
    entries: &[Entry],
    policy: Policy<'_>,
) -> Result<()> {
    const ALL: u32 = 0x001f01ff;
    let trusted = |sid: &str| {
        sid == policy.current
            || sid == "S-1-5-18"
            || sid == "S-1-5-32-544"
            || policy.trusted_installer == Some(sid)
    };
    if entries.is_empty()
        || entries.len() > 64
        || (policy.private && (!protected || owner != policy.current))
        || (!policy.private && !trusted(owner))
    {
        return Err(UNPROVEN);
    }
    let (mut owner_access, mut system_access) = (0, 0);
    for entry in entries {
        if entry.kind != 0 || entry.flags & !0x1f != 0 || entry.mask & !(ALL | 0xf0000000) != 0 {
            return Err(UNPROVEN);
        }
        let owner_rights = entry.sid == "S-1-3-4";
        if policy.private && entry.sid != policy.current && !owner_rights && entry.sid != "S-1-5-18"
        {
            return Err(UNPROVEN);
        }
        if entry.flags & 8 != 0 {
            continue;
        } // INHERIT_ONLY grants no rights here.
        let mut mask = entry.mask & ALL;
        for (generic, mapped) in [
            (0x10000000, ALL),
            (0x40000000, 0x120116),
            (0x80000000, 0x120089),
            (0x20000000, 0x1200a0),
        ] {
            if entry.mask & generic != 0 {
                mask |= mapped;
            }
        }
        if entry.sid == policy.current || owner_rights {
            owner_access |= mask;
        }
        if entry.sid == "S-1-5-18" {
            system_access |= mask;
        }
        // Existing environmental directories may be readable by other users.
        // Their grants cannot change security, delete this object/its child, or
        // create a reserved absent component. No ACL mutation occurs here.
        let dangerous = 0x000d0150 | if policy.child_absent { 6 } else { 0 };
        if !policy.private && !trusted(&entry.sid) && !owner_rights && mask & dangerous != 0 {
            return Err(UNPROVEN);
        }
    }
    if policy.private && (owner_access & ALL != ALL || system_access & ALL != ALL) {
        return Err(UNPROVEN);
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    const SID: &str = "S-1-5-21-123-456-789-1001";
    fn entry(sid: &str, mask: u32) -> Entry {
        Entry {
            kind: 0,
            flags: 0,
            mask,
            sid: sid.into(),
        }
    }
    fn private() -> Policy<'static> {
        Policy {
            current: SID,
            trusted_installer: None,
            private: true,
            child_absent: false,
        }
    }
    fn full() -> Vec<Entry> {
        vec![entry(SID, 0x1f01ff), entry("S-1-5-18", 0x1f01ff)]
    }
    #[test]
    fn protected_acl_policy_refuses_unprotected_or_ineffective_grants() {
        assert!(validate(SID, false, &full(), private()).is_err());
        let mut entries = full();
        entries[0].flags = 8;
        assert!(validate(SID, true, &entries, private()).is_err());
        let mut entries = full();
        entries[1].mask = 0x120089;
        assert!(validate(SID, true, &entries, private()).is_err());
        assert!(validate(SID, true, &full(), private()).is_ok());
    }
    #[test]
    fn protected_acl_policy_refuses_foreign_null_public_unknown_and_oversized() {
        assert!(validate("S-1-5-21-999", true, &full(), private()).is_err());
        assert!(validate(SID, true, &[], private()).is_err());
        let mut entries = full();
        entries.push(entry("S-1-1-0", 0x120089));
        assert!(validate(SID, true, &entries, private()).is_err());
        let mut entries = full();
        entries[0].kind = 1;
        assert!(validate(SID, true, &entries, private()).is_err());
        let mut entries = full();
        entries[0].mask |= 0x02000000;
        assert!(validate(SID, true, &entries, private()).is_err());
        assert!(validate(
            SID,
            true,
            &(0..65).map(|_| entry(SID, 0x1f01ff)).collect::<Vec<_>>(),
            private()
        )
        .is_err());
    }
    #[test]
    fn ancestor_acl_policy_checks_effective_child_and_security_rights() {
        let policy = Policy {
            private: false,
            ..private()
        };
        for mask in [
            0x40, 0x10000, 0x40000, 0x80000, 0x40000000, 0x10000000, 0x10, 0x100,
        ] {
            let mut entries = full();
            entries.push(entry("S-1-5-21-999", mask));
            assert!(validate(SID, false, &entries, policy).is_err());
        }
        let mut entries = full();
        entries.push(entry("S-1-1-0", 0x120089));
        assert!(validate(SID, false, &entries, policy).is_ok());
        entries.last_mut().unwrap().mask = 6;
        assert!(validate(
            SID,
            false,
            &entries,
            Policy {
                child_absent: true,
                ..policy
            }
        )
        .is_err());
        assert!(validate(SID, false, &entries, policy).is_ok());
        entries.last_mut().unwrap().flags = 8;
        assert!(validate(
            SID,
            false,
            &entries,
            Policy {
                child_absent: true,
                ..policy
            }
        )
        .is_ok());
        assert!(validate("S-1-5-32-544", false, &full(), policy).is_ok());
    }

    #[test]
    fn ancestor_file_rights_distinguish_creation_from_deletion_and_security_writes() {
        // File-system masks, not directory-service names: SDDL DC is 0x2,
        // while FILE_DELETE_CHILD is 0x40. Generic write also grants EA/attribute writes.
        for (right, mask, existing_child_allowed) in [
            ("FILE_ADD_FILE", 0x2, true),
            ("FILE_ADD_SUBDIRECTORY", 0x4, true),
            ("both creation rights", 0x6, true),
            ("FILE_DELETE_CHILD", 0x40, false),
            ("DELETE", 0x10000, false),
            ("WRITE_DAC", 0x40000, false),
            ("WRITE_OWNER", 0x80000, false),
            ("GENERIC_WRITE", 0x40000000, false),
        ] {
            let mut entries = full();
            entries.push(entry("S-1-5-21-999", mask));
            for child_absent in [false, true] {
                let result = validate(
                    SID,
                    false,
                    &entries,
                    Policy {
                        private: false,
                        child_absent,
                        ..private()
                    },
                );
                assert_eq!(
                    result,
                    if existing_child_allowed && !child_absent {
                        Ok(())
                    } else {
                        Err(UNPROVEN)
                    },
                    "{right} ({mask:#x}), child_absent={child_absent}",
                );
            }
        }
    }
}
