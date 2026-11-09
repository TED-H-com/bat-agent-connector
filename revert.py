import sys

with open('tests/test_core_workflows.py', 'r') as f:
    lines = f.readlines()

new_lines = []
skip = False
for line in lines:
    if 'async def mock_route(' in line:
        skip = True
        continue
    if skip and 'pass' in line:
        continue
    if skip and 'coordinator._route = mock_route' in line:
        skip = False
        continue
    new_lines.append(line)

with open('tests/test_core_workflows.py', 'w') as f:
    f.writelines(new_lines)
