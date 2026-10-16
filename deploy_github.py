#!/usr/bin/env python3
"""First deployment to GitHub Pages. Requires Python 3.11+, Git and authenticated gh.

Run --check for an offline file check. Run --public to explicitly authorize a new
public repository and website. This script never reads or prints an access token.
It refuses existing repositories unless its local resume state matches the repo ID.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent
STATE_NAME = '.deploy-state.json'
UPLOAD_PATHS = (
    '.github/workflows/daily.yml', '.gitignore', 'README.md', 'START_HERE.md',
    'config.yaml', 'overrides.yaml', 'requirements.txt', 'run.py',
    'deploy_github.py', 'radar/__init__.py', 'radar/collect.py', 'radar/core.py',
    'web/index.html', 'web/style.css', 'web/app.js', 'tests/test_radar.py',
    'tests/test_deploy.py', 'data/store.json', 'data/state.json', 'data/preview.json',
    'deploy/README.md', 'preview.html', 'TEST_REPORT.json', 'DEPLOY_TEST_REPORT.json',
)

class DeployError(RuntimeError):
    pass


def command(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env['GH_HOST'] = 'github.com'
    env['GH_PROMPT_DISABLED'] = '1'
    try:
        result = subprocess.run(args, cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding='utf-8', errors='replace', timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DeployError(f'Unable to run {args[0]}: {exc}') from exc
    if check and result.returncode:
        raise DeployError(f"Command failed: {' '.join(args)}\n{result.stderr.strip() or result.stdout.strip()}")
    return result


def api(endpoint: str, method: str = 'GET', fields: dict[str, str] | None = None,
        missing_ok: bool = False) -> dict[str, Any] | None:
    args = ['gh', 'api', '--hostname', 'github.com', '--method', method,
            '-H', 'Accept: application/vnd.github+json', endpoint]
    for key, value in (fields or {}).items():
        args += ['-f', f'{key}={value}']
    result = command(*args, check=False)
    if result.returncode:
        if missing_ok and 'HTTP 404' in result.stderr:
            return None
        raise DeployError(f'GitHub API {method} {endpoint} failed:\n{result.stderr.strip()}')
    return json.loads(result.stdout) if result.stdout.strip() else {}


def validate_name(value: str) -> str:
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,99}', value) or value in {'.', '..'}:
        raise DeployError('Repository name must use letters, numbers, dots, underscores or hyphens (1-100 characters).')
    if value.lower().endswith('.github.io'):
        raise DeployError('Use a project name such as paper-radar, not an account-wide .github.io repository.')
    return value


def check_project(root: Path) -> list[str]:
    missing = [p for p in UPLOAD_PATHS if not (root / p).is_file()]
    if missing:
        raise DeployError('Missing project files; extract the full ZIP first:\n' + '\n'.join(missing))
    for path in UPLOAD_PATHS:
        if (root / path).is_symlink() or not (root / path).resolve().is_relative_to(root.resolve()):
            raise DeployError(f'Refusing a symlink or external file: {path}')
    return list(UPLOAD_PATHS)


def require_owner(actual: str, expected: str | None) -> None:
    if expected and actual.casefold() != expected.casefold():
        raise DeployError(f'Logged in as {actual}, not {expected}. Switch the active gh account first.')


def save_state(state: dict[str, Any]) -> None:
    path = ROOT / STATE_NAME
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    tmp.replace(path)


def deploy(owner_expected: str | None, repo_name: str) -> str:
    files = check_project(ROOT)
    validate_name(repo_name)
    for tool in ('git', 'gh'):
        if not shutil.which(tool):
            raise DeployError(f'{tool} is not installed or not on PATH. See START_HERE.md.')
    command('gh', 'auth', 'status', '--hostname', 'github.com')
    user = api('user')
    if not user or not user.get('login'):
        raise DeployError('Cannot determine the authenticated GitHub account.')
    owner = str(user['login'])
    require_owner(owner, owner_expected)
    full = f'{owner}/{repo_name}'
    state_path = ROOT / STATE_NAME
    state = json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
    if state and state.get('repository') != full:
        raise DeployError('This folder belongs to another deployment. Extract a fresh copy of the ZIP.')
    local_repo = command('git', 'rev-parse', '--show-toplevel', check=False)
    if local_repo.returncode == 0:
        if Path(local_repo.stdout.strip()).resolve() != ROOT or not state:
            raise DeployError('Refusing an unrelated existing Git repository. Extract into a new standalone folder.')
    remote = api(f'repos/{full}', missing_ok=True)
    if remote:
        if not state or state.get('repository_id') != remote['id']:
            raise DeployError(f'{full} already exists and was not created by this script in this folder. Nothing was overwritten. Use a different --repo name.')
        if remote.get('private'):
            raise DeployError('The existing repository is private. This script will not change its visibility.')
    elif state:
        raise DeployError('The saved repository is no longer accessible. Check your account and permissions; nothing was recreated.')
    else:
        print(f'Creating PUBLIC repository: {full}', flush=True)
        print('Project code, keywords and paper metadata will be public. No files outside the upload allowlist will be added.', flush=True)
        command('gh', 'repo', 'create', full, '--public', '--disable-wiki',
                '--description', 'Daily research paper radar: arXiv, journals and conference proceedings')
        remote = api(f'repos/{full}')
        state = {'repository': full, 'repository_id': remote['id'], 'pushed': False}
        save_state(state)
    if not state.get('pushed'):
        if not (ROOT / '.git').exists():
            command('git', 'init', '-b', 'main')
        branch = command('git', 'symbolic-ref', '--short', 'HEAD').stdout.strip()
        if branch != 'main':
            raise DeployError('Expected local branch main. No branch was renamed or overwritten.')
        remote_url = f'https://github.com/{full}.git'
        existing_origin = command('git', 'remote', 'get-url', 'origin', check=False)
        if existing_origin.returncode:
            command('git', 'remote', 'add', 'origin', remote_url)
        elif existing_origin.stdout.strip() != remote_url:
            raise DeployError('Local origin does not match the intended repository; refusing to push.')
        command('git', 'config', '--local', 'user.name', owner)
        command('git', 'config', '--local', 'user.email', f"{user['id']}+{owner}@users.noreply.github.com")
        command('git', 'add', '--', *files)
        # Never publish unexpected pre-existing tracked files, even on a resumed run.
        tracked = set(command('git', 'ls-files').stdout.splitlines())
        if tracked - set(files):
            raise DeployError('Unexpected tracked files detected; refusing public upload.')
        changed = command('git', 'diff', '--cached', '--quiet', check=False)
        if changed.returncode == 1:
            # Configure Pages before the first deliberate workflow dispatch.
            command('git', 'commit', '-m', 'Initialize Paper Radar [skip ci]')
        elif changed.returncode != 0:
            raise DeployError('Unable to inspect staged files.')
        print('Uploading the project (without force-push)...', flush=True)
        command('git', '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
                'push', '--set-upstream', 'origin', 'main')
        state['pushed'] = True
        save_state(state)
    api(f'repos/{full}', method='PATCH', fields={'default_branch': 'main'})
    pages = api(f'repos/{full}/pages', missing_ok=True)
    if pages is None:
        api(f'repos/{full}/pages', method='POST', fields={'build_type': 'workflow'})
    elif pages.get('build_type') != 'workflow':
        api(f'repos/{full}/pages', method='PUT', fields={'build_type': 'workflow'})
    print('Pages is configured to deploy with GitHub Actions.', flush=True)
    # Dispatch through the REST endpoint by filename. Retry only a transient 404
    # caused by GitHub indexing a newly pushed workflow, never a permission error.
    import time
    for attempt in range(4):
        try:
            api(f'repos/{full}/actions/workflows/daily.yml/dispatches', method='POST', fields={'ref': 'main'})
            break
        except DeployError as exc:
            if 'HTTP 404' not in str(exc) or attempt == 3:
                raise
            time.sleep(3)
    print('\nFirst workflow requested. This does NOT prove that collection or deployment has succeeded.')
    print(f'Check progress and the deploy job URL: https://github.com/{full}/actions')
    print(f'Expected site address AFTER a successful deployment: https://{owner.lower()}.github.io/{repo_name}/')
    print('Check both the deploy job and the website source-status panel.')
    return full


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--owner', help='Expected GitHub login; refuse deployment if the active account differs')
    parser.add_argument('--repo', default='paper-radar', help='Name of the new repository (default: paper-radar)')
    parser.add_argument('--public', action='store_true', help='Explicitly authorize publication as a public repository and website')
    parser.add_argument('--check', action='store_true', help='Offline project check only; no GitHub calls or writes')
    args = parser.parse_args(argv)
    try:
        validate_name(args.repo)
        if args.check:
            paths = check_project(ROOT)
            print(f'Offline project check passed: {len(paths)} upload files. No GitHub changes made.')
            return 0
        if not args.public:
            raise DeployError('No publication authorized. Read START_HERE.md and pass --public to create a public repository.')
        deploy(args.owner, args.repo)
        return 0
    except (DeployError, ValueError, KeyError, OSError) as exc:
        print(f'\nSTOPPED: {exc}', file=sys.stderr)
        print('No force-push, deletion or visibility change was performed. Fix the reported problem before retrying.', file=sys.stderr)
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
