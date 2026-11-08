with open('tests/test_pm_schedule.py', 'r') as f:
    content = f.read()

content = content.replace('    if not body["as_of"].startswith("2026-11-08"):\n        assert "2026-11-08" not in json.dumps(body)', '    assert "2026-11-08" not in json.dumps(body)')
content = content.replace('    if body["as_of"][:10] not in {"2026-11-01", "2026-11-08", instant(TASK_AT)[:10]}:\n        assert body["as_of"][:10] not in recorded', '    assert body["as_of"][:10] not in recorded')

with open('tests/test_pm_schedule.py', 'w') as f:
    f.write(content)
