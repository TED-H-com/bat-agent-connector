"""Conservative process-construction guard; run from any directory (stdlib only)."""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def violations(path: str, source: str):
    """Report policy bypasses. Raw imports are forbidden outside reviewed helpers."""
    suffix = Path(path).suffix.lower()
    rules = []
    if suffix == '.rs':
        rules = [(r'\bCommand\s*::\s*new\s*\(', 'use hidden_command/hidden_tokio_command'),
                 (r'\.creation_flags\((?![^\n]*hidden_flags\()', 'OR flags through hidden_flags')]
        for alias in re.findall(r'\bCommand\s+as\s+(\w+)', source):
            for match in re.finditer(r'\b' + re.escape(alias) + r'\s*::\s*new\s*\(', source):
                yield source.count('\n', 0, match.start()) + 1, match.group(), 'use hidden command constructors'
    elif suffix in {'.js', '.ts', '.mjs', '.cjs', '.tsx'}:
        rules = [(r'''["'](?:node:)?child_process["']''', 'use hidden-child-process.mjs'),
                 (r'\bwindowsHide\s*:\s*false', 'windowsHide must be true'),
                 (r'\b(?:Command\.sidecar|Command\.create|\.shell\(\))', 'review Tauri shell launch hiding')]
    elif suffix == '.ps1':
        rules = [(r'(?im)^\s*[^#\n]*\bStart-Process\b(?![^\n]*-WindowStyle\s+Hidden)', 'use -WindowStyle Hidden'),
                 (r'(?im)^\s*[^#\n]*\bNew-ScheduledTaskSettingsSet\b(?![^\n]*-Hidden\b)', 'task settings require -Hidden'),
                 (r'(?im)^\s*[^#\n]*\b(?:powershell|pwsh)(?:\.exe)?\s+(?![^\n]*-WindowStyle\s+Hidden)', 'PowerShell requires hidden flags'),
                 (r'(?im)^\s*&\s+.+', 'use windowless ProcessStartInfo for external executables')]
        if re.search(r'(?im)^\s*[^#\n]*\b(?:Register-ScheduledTask|schtasks(?:\.exe)?\s+/create)\b', source) and not re.search(r'-Hidden\b|<Hidden>\s*true', source, re.I):
            yield 1, 'scheduled task registration', 'scheduled task requires hidden settings and action review'
        for match in re.finditer(r'\[(?:System\.)?Diagnostics\.ProcessStartInfo\]::new\(', source, re.I):
            end = source.find('[Diagnostics.Process]::Start(', match.end())
            block = source[match.end():end if end >= 0 else len(source)]
            if not re.search(r'\.CreateNoWindow\s*=\s*\$true', block, re.I):
                yield source.count('\n', 0, match.start()) + 1, match.group(), 'set CreateNoWindow=true'
    elif suffix == '.xml' and re.search(r'<(?:\w+:)?Task\b', source):
        if not re.search(r'<(?:\w+:)?Hidden>\s*true\s*</', source, re.I):
            yield 1, '<Task', 'scheduled task requires Hidden=true'
        if re.search(r'<(?:\w+:)?Command>[^<]*(?:powershell|pwsh|cmd|node)(?:\.exe)?\s*</', source, re.I):
            yield 1, '<Command>', 'console task action requires a windowless wrapper'
    elif suffix == '.py':
        try:
            tree = ast.parse(source)
        except SyntaxError:
            yield 1, 'syntax', 'cannot inspect Python source'
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {'run', 'Popen', 'create_subprocess_exec', 'create_subprocess_shell'} and ast.get_source_segment(source, node) in {
                'subprocess.run', 'subprocess.Popen', 'asyncio.create_subprocess_exec', 'asyncio.create_subprocess_shell'
            }:
                yield node.lineno, ast.get_source_segment(source, node), 'use hidden_process'
    for pattern, reason in rules:
        for match in re.finditer(pattern, source):
            yield source.count('\n', 0, match.start()) + 1, source.splitlines()[source.count('\n', 0, match.start())].strip(), reason


def scan(root: Path, paths, allowlist):
    failures = []
    for path in paths:
        if path.startswith('desktop/vendor/'):
            continue  # third-party source, not maintained launch policy
        file = root / path
        if not file.is_file():
            continue
        source = file.read_text(encoding='utf-8')
        for line, matched, reason in violations(path, source):
            allowed = any(entry['path'] == path and re.search(entry['pattern'], matched)
                          and entry['justification'].strip() for entry in allowlist)
            if not allowed:
                failures.append(f'{path}:{line}: {reason}')
    return failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    paths = subprocess.check_output(['git',  # noqa: S607 - read-only repository inventory
                                     'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                                    cwd=args.root, encoding='utf-8',
                                    creationflags=0x08000000 if os.name == 'nt' else 0).split('\0')
    paths = [p for p in paths if Path(p).suffix.lower() in {'.rs', '.ts', '.tsx', '.js', '.mjs', '.cjs', '.ps1', '.xml'}
             or p.startswith('src/bat_agent_connector/') and p.endswith('.py')]
    allowlist = json.loads((args.root / 'scripts/hidden-spawns-allowlist.json').read_text())
    failures = scan(args.root, paths, allowlist)
    print('\n'.join(failures) if failures else 'Hidden process policy passed')
    return bool(failures)


if __name__ == '__main__':
    raise SystemExit(main())
