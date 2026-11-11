use super::*;

fn input(f: &Fixture) -> NativeInput {
    NativeInput {
        pointer: f.reader.expected.scope.pointer.clone(),
        kit_root: f.reader.expected.scope.kit_root.clone(),
        role_record: f.reader.expected.scope.startup_role_record.clone(),
        startup: f.reader.expected.startup.clone(),
        roaming: f.root.clone(),
        startup_directory: f.root.join("Startup"),
        user: f.root.join("user"),
    }
}
#[test]
fn context_new_receipt_is_minted_once_and_existing_receipt_is_reproved() {
    let f = writer_tests::fresh();
    let calls = Cell::new(0);
    let mut mint = || {
        calls.set(calls.get() + 1);
        ["c".repeat(32), "d".repeat(32)]
    };
    let mut p = CreationPlatform::from_native_input(
        input(&f),
        f.reader.security.clone(),
        &|| Ok(()),
        &mut mint,
    )
    .unwrap();
    let owner = p
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
            evidence::capture(w, &owner)?
                .resolve(&[], &[])?
                .baseline()?;
            Ok(owner)
        })
        .unwrap();
    assert_eq!(calls.get(), 1);
    assert_eq!(owner.installation_id, "c".repeat(32));
    drop(p);
    let mut p = CreationPlatform::from_native_input(
        input(&f),
        f.reader.security.clone(),
        &|| Ok(()),
        &mut || panic!("existing evidence never mints"),
    )
    .unwrap();
    let before = std::fs::read(p.reader.key_path(&EvidenceKey::Provisioning).unwrap()).unwrap();
    assert_eq!(
        p.with_writer(|w| w.reserve(&mut || panic!("existing evidence never reserves")))
            .unwrap(),
        owner
    );
    assert_eq!(
        std::fs::read(p.reader.key_path(&EvidenceKey::Provisioning).unwrap()).unwrap(),
        before
    );
}
#[test]
fn context_startup_sid_and_incomplete_namespace_never_inherit() {
    let f = writer_tests::fresh();
    let mut p = CreationPlatform::from_native_input(
        input(&f),
        f.reader.security.clone(),
        &|| Ok(()),
        &mut || ["c".repeat(32), "d".repeat(32)],
    )
    .unwrap();
    p.with_writer(|w| w.reserve(&mut || Ok(()))).unwrap();
    drop(p);
    for field in 0..3 {
        let mut native = input(&f);
        if field == 0 {
            native.startup.installation_ref = "11111111-1111-4111-8111-111111111111".into();
        }
        if field == 1 {
            native.startup.generation += 1;
        }
        if field == 2 {
            f.reader.security.login.borrow_mut().owner_sid = "S-1-5-21-999".into();
        }
        assert!(CreationPlatform::from_native_input(
            native,
            f.reader.security.clone(),
            &|| Ok(()),
            &mut || panic!("changed identity never mints")
        )
        .is_err());
    }
}
