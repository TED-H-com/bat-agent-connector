"""Private extension of the shared locked Git executor; no executable request code."""
if __name__ != '__main__':
    import base64
    import json
    import os
    import subprocess
    import sys

    from .repository_sync_host import execute, fetch_source, git, real


def physical(path):
    real(path)
    item = os.stat(path)
    return {'path': path, 'device': item.st_dev, 'inode': item.st_ino}


def observe(stage, req, value):
    if stage == 'existing':
        if value:
            creation = req.get('creation')
            if not creation:
                raise ValueError('NATIVE_CARRIER_CREATION_UNPROVEN')
            if os.path.lexists(req['worktree_path']) and 'worktree' not in creation:
                raise ValueError('NATIVE_CARRIER_CREATION_UNPROVEN')
            if creation['clone'] != physical(req['clone_path']):
                raise ValueError('NATIVE_CARRIER_CHANGED')
        elif req.get('creation'):
            raise ValueError('NATIVE_CARRIER_CHANGED')
        return value
    facts = {'clone': physical(req['clone_path'])}
    path = req['worktree_path']
    if os.path.lexists(path):
        worktrees = {line[9:] for line in git(req['clone_path'], 'worktree', 'list', '--porcelain').splitlines()
                     if line.startswith('worktree ')}
        if worktrees != {req['clone_path'], path}:
            raise ValueError('NATIVE_CARRIER_CHANGED')
        gitdir = git(path, 'rev-parse', '--absolute-git-dir')
        if not gitdir.startswith(req['clone_path'] + '/.git/worktrees/'):
            raise ValueError('NATIVE_CARRIER_CHANGED')
        facts.update(worktree=physical(path), git_dir=physical(gitdir))
    return {**value, 'physical': facts}


def copy_source(req):
    if req['source_ref'] is not None:
        return fetch_source(req)
    source = req['remote_url']
    real(source)
    common = git(source, 'rev-parse', '--path-format=absolute', '--git-common-dir')
    real(common)
    # Refuse external object stores and lazy fetching before reading any commit.
    keys = git(source, 'config', '--local', '--name-only', '--list').lower().splitlines()
    if (any(k.endswith('.promisor') or k == 'extensions.partialclone'
            or k.startswith(('uploadpack.', 'include.', 'includeif.')) for k in keys)
            or any(os.path.lexists(common + '/' + p) for p in ('objects/info/alternates', 'objects/info/http-alternates', 'shallow', 'info/grafts'))
            or git(source, 'for-each-ref', 'refs/replace')
            or git(source, 'cat-file', '-t', req['source_sha']) != 'commit'):
        raise ValueError('NATIVE_SOURCE_UNPROVEN')
    # Fetch copies objects into the new clone; it never shares or hardlinks a store.
    git(req['clone_path'], 'fetch', '--no-tags', '--no-write-fetch-head', '--no-recurse-submodules', source, req['source_sha'])


if __name__ == '__main__':
    try:
        request = json.loads(base64.b64decode(sys.argv[1]))
        print(json.dumps({'result': execute(request, observe=observe, fetch_source=copy_source)}), flush=True)
    except (ValueError, OSError, subprocess.SubprocessError):
        error = str(sys.exc_info()[1])
        code = error if error.startswith(('NATIVE_', 'REPOSITORY_')) else 'REPOSITORY_HOST_UNCERTAIN'
        print(json.dumps({'error': code}), flush=True)
