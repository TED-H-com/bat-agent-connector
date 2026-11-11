use super::{reservation::snapshot, *};
use crate::connection_publish::{AbandonDecision, Boundary, Error, FrozenPlanRef, Publisher};

fn publisher(f: &Fixture) -> Publisher {
    Publisher::new(
        &f.reader.expected.scope.pointer,
        &f.root.join("Startup"),
        &f.root.join("user"),
    )
    .unwrap()
}
fn plan(f: &Fixture) -> crate::connection_setup::PublicationPlan {
    let mut input = generator::setup();
    input.root = f.reader.expected.scope.kit_root.clone();
    input.owner = f.owner.clone();
    crate::connection_setup::build(&input).unwrap()
}

#[test]
fn preparation_expires_between_levels_and_preserves_the_recorded_prefix() {
    let f = fresh();
    let mut p = f.platform();
    p.with_writer(|w| {
        w.reserve(&mut || Ok(()))?;
        w.prepare_publication_directories(&mut || Ok(()))?;
        let kit = w.platform.reader.expected.scope.kit_root.clone();
        let mut calls = 0;
        assert_eq!(
            publication::Platform::prepare_directory(
                w.platform,
                &kit.join("client/bat-profiles"),
                &f.owner,
                &mut || {
                    calls += 1;
                    if calls == 2 {
                        Err("EXPIRED")
                    } else {
                        Ok(())
                    }
                }
            ),
            Err(Error::Authorization { code: "EXPIRED" })
        );
        assert_eq!(calls, 2);
        assert!(kit.is_dir());
        assert!(!kit.join("client").exists());
        w.platform
            .reader
            .directory(&kit, parse_owner_reference(&f.owner)?)?;
        evidence::capture(w, &f.owner)?
            .resolve(&[], &[])?
            .baseline()?;
        Ok(())
    })
    .unwrap();
}

#[test]
fn preparation_read_only_inspect_and_load_never_create_private_directories() {
    let f = fresh();
    let mut p = f.platform();
    p.with_writer(|w| w.reserve(&mut || Ok(()))).unwrap();
    let plan = plan(&f);
    let bytes = crate::connection_setup::frozen::encode(&plan).unwrap();
    let reference = FrozenPlanRef {
        review_digest: plan.review_digest.clone(),
        sha256: digest(&bytes),
    };
    let before = snapshot(&f.root);
    assert!(publisher(&f)
        .load_plan(&reference, &f.owner, &mut p)
        .is_err());
    assert_eq!(snapshot(&f.root), before);
    let _ = publisher(&f).inspect(&plan, &mut p);
    assert_eq!(snapshot(&f.root), before);
    for path in [
        &plan.root,
        &f.reader
            .expected
            .scope
            .state_root
            .join("fleet-publication-plans"),
    ] {
        assert!(!path.exists());
    }
}

#[test]
fn preparation_clean_reservation_original_history_and_link_prove_owned() {
    let f = fresh();
    let mut p = f.platform();
    let baseline = p
        .with_writer(|w| {
            let owner = w.reserve(&mut || Ok(()))?;
            let parent = w
                .platform
                .reader
                .expected
                .path(Artifact::ProfileIndex)
                .parent()
                .unwrap()
                .to_path_buf();
            w.prepare_parents(&parent, &mut || Ok(()))?;
            w.prepare_publication_directories(&mut || Ok(()))?;
            for path in w.platform.reader.namespace_directories()? {
                w.platform
                    .reader
                    .directory(&path, parse_owner_reference(&owner)?)?;
            }
            evidence::capture(w, &owner)?.resolve(&[], &[])?.baseline()
        })
        .unwrap();
    // Publisher APIs take their own guards; the writer scope has ended.
    let plan = plan(&f);
    let publisher = publisher(&f);
    let frozen = publisher.export_plan(&plan, &mut p).unwrap();
    p.with_writer(|w| {
        let captured = evidence::capture(w, &f.owner)?;
        let record = Record::Publication(PublicationLink {
            binding: w
                .platform
                .reader
                .expected
                .binding(captured.receipt_sha256(), 7)?,
            predecessor_sha256: captured.receipt_sha256().into(),
            predecessor_terminal: None,
            frozen: frozen.clone(),
            generator_version: plan.generator_version,
            baseline,
            acceptance_reference: "native-review-fixture".into(),
        });
        w.append(&f.owner, &record, &mut || Ok(()))?;
        Ok(())
    })
    .unwrap();
    assert_eq!(
        publisher.publish_authorized(&plan, &mut p, &mut |_, _| Ok(()), &mut |point, artifact| {
            if point == Boundary::AfterIntended && artifact == Artifact::Inventory {
                Err("INTERRUPTED")
            } else {
                Ok(())
            }
        }),
        Err(Error::Gate("INTERRUPTED"))
    );
    drop(p);
    // Neither an in-memory history nor the publisher's byte classification owns a slot.
    let mut p = f.platform();
    p.reader.security.held.set(true);
    let histories = p.histories().unwrap();
    assert_eq!(histories.len(), 1);
    assert_eq!(histories[0].frozen().reference(), &frozen);
    assert_eq!(histories[0].reached(Artifact::Inventory), Some(false));
    let path = p.reader.expected.path(Artifact::Inventory);
    assert_eq!(
        p.ownership(&f.owner, Artifact::Inventory, path, None)
            .unwrap(),
        FileOwnership::Owned {
            owner: f.owner.clone(),
            artifact: Artifact::Inventory,
            sha256: digest(b"")
        }
    );
    evidence::capture(&p.reader, &f.owner)
        .unwrap()
        .resolve(&histories, &[])
        .unwrap();
    p.reader.security.held.set(false);
    // Abandonment's bounded review child uses the same recorded level.
    p.with_writer(|w| w.prepare_decision_directory(&frozen, &mut || Ok(())))
        .unwrap();
    let decision = publisher
        .abandon_remaining(
            &plan,
            &AbandonDecision {
                reference: "native-abandon-fixture".into(),
                recorded_at_unix_ms: 123,
            },
            &mut p,
            &mut |_| Ok(()),
        )
        .unwrap();
    assert!(decision.is_file());
}
