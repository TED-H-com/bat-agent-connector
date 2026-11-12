# ``` Scope and roadmap report ```

```text
Historical snapshot · fixed source selection
```

```text
A snapshot ID alone does not identify this report: the observation fixes its baseline and forecast too.
```

```text
This report prepares discussion. It does not complete a Review, authorize work, verify quality, or publish anything.
```

```text
Assembly time is not a fresh check of every source. Each saved observation retains its own time.
```

```text
Actual finish dates are unknown: no actual-date record exists yet. Commit, merge, review and acceptance times are not actual finish dates.
```

```text
Requirement IDs belong to their exact plan revision. Shared work and repeated milestones are not additional deliverables. Dependencies do not predict dates or a critical path.
```

```text
Forecast dates are those selected by this observation, not the current forecast. Quality describes original selected assessments; current applicability is not evaluated.
```

## ``` Fixed identity and coverage ```

```text
{
  "baseline_digest": "8888888888888888888888888888888888888888888888888888888888888888",
  "baseline_revision_id": "pmrb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "forecast_digest": null,
  "forecast_revision_id": null,
  "observation_digest": "8e212ab6b672589d179422a716dfd462e8246513e6cfbc9e77a7a2f24b659701",
  "observation_revision_id": "pmro_11111111111111111111111111111111",
  "snapshot_digest": "335caf623fcc0b71ac5f804cc8a2c26b35dba2663d7fd198679f4e6f1cd7c7cd",
  "snapshot_id": "pmsnap_22222222222222222222222222222222",
  "snapshot_view_digest": "7777777777777777777777777777777777777777777777777777777777777777",
  "source_set_digest": "9999999999999999999999999999999999999999999999999999999999999999",
  "view_digest": "6a4f04cc51c3d75a8030c53c1cc4f5a3bcd60cfc21fa686477090a0aa2163b09",
  "workspace_id": "ws_66666666666666666666666666666666"
}
```

```text
Workspace: Synthetic report
```

```text
Snapshot assembled at: 2026-11-12T12:00:00 UTC
```

```text
Central journal cursor: 42
```

## ``` Baseline at snapshot time ```

```text
Accepted plan
```

```text
{
  "document_revision": "pmrev_33333333333333333333333333333333",
  "scope_revision": "pmscope_55555555555555555555555555555555",
  "status": "accepted"
}
```

```text
Login 登入 é 👩‍💻 🇹🇼 🫨 𞓬
```

## ``` Accepted requirements ```

### ``` Requirement: login.exact ```

```text
{
  "acceptance_input_ids": [
    "criterion"
  ],
  "id": "login.exact"
}
```

### ``` Original scope inputs ```

````text
[
  {
    "input_id": "criterion",
    "status": "user_confirmed",
    "text": "Reject <script> and ``` delimiters; https://example.invalid stays text"
  }
]
````

```text
{
  "scope": [
    "Login only; other scope 未知"
  ]
}
```

## ``` Selected scheduling baseline ```

```text
Fixed roadmap
```

```text
No inferred dates
```

```text
pmrb_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

### ``` Milestone: ready · Login 登入 é 👩‍💻 🇹🇼 🫨 𞓬 ```

```text
Original baseline date: Unknown
```

```text
Selected baseline date: 2026-11-12
```

```text
Forecast date: Unknown
```

```text
Actual finish date: Unknown
```

```text
Dependencies: []
```

```text
{
  "baseline_date": "2026-11-12",
  "depends_on": [],
  "milestone_id": "ready",
  "original_baseline_date": null,
  "plan_document_digest": "4444444444444444444444444444444444444444444444444444444444444444",
  "plan_revision_id": "pmrev_33333333333333333333333333333333",
  "requirement_ids": [
    "login.exact"
  ],
  "scope_revision": "pmscope_55555555555555555555555555555555",
  "title": "Login 登入 é 👩‍💻 🇹🇼 🫨 𞓬",
  "work_binding_ids": []
}
```

```text
Original quality and coverage
```

```text
{
  "actual": {
    "actual_finish_date": null,
    "completion": "unassessed",
    "dod": "unassessed",
    "facts": []
  },
  "coverage": {
    "work_mapping": "unmapped"
  },
  "evidence_ids": [],
  "milestone_id": "ready"
}
```

```text
{
  "changes": {},
  "forecast": null
}
```

## ``` Original quality and coverage ```

```text
Unknown
```

```text
These attributed assessments were explicitly selected at capture time. Later conclusions and current applicability are not substituted.
```

```text
{
  "content_truncated": false,
  "projection": "roadmap_report",
  "snapshot": {
    "freshness": "per_source_saved_times",
    "work_truncated": false
  },
  "visibility": {
    "filtered": false,
    "source_policy": "whole_original_closure"
  }
}
```

## ``` Source references and recorded facts ```

```text
[]
```

```text
{
  "coverage": {
    "freshness": "per_source_saved_times",
    "work_truncated": false
  },
  "work": []
}
```
