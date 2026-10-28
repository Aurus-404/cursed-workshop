# Publishing a GitHub release

Official repository: https://github.com/Aurus-404/cursed-workshop

Use Windows x64, Python 3.12.10, Node.js 24 and an authenticated GitHub CLI (`gh auth status`).
No Steam upload is part of this procedure. Keep the single local program at
`dist/CursedWorkshop/CursedWorkshop.exe`. Close it before building; never make a second distribution.

## Prepare the version

Use the intended real version in `workshop/__init__.py`, `package.json`, `package-lock.json` (including
its root package) and `pyproject.toml`. Add `docs/releases/<version>.md` describing the changes and validation
limits. Review the public files for secrets, real saves, player data and third-party assets. Update
`requirements-build.txt` and third-party notices when dependencies change. Do not select a project license
without the owner's decision.

```powershell
npm ci
python -m pip install --target .build-deps -r requirements-build.txt
npm run build
python scripts/prepare_github_source.py
```

Commit the intended source before building. In a normal clone of the public repository, use normal Git commits
and push `main`. The source allowlist is for migration from the private development repository; extend it only
after reviewing new files. All application/frontend files and synthetic tests are included.

## This shared private development workspace

The first migration preserves the private repository, its staged changes and its full history. A second Git
metadata directory at `outputs/checks/github-public.git` tracks the public source directly in this same working
folder. It does not create another application checkout or build. For subsequent releases here:

The public metadata uses the owner's GitHub username and GitHub noreply email for commits and tags;
personal Git author email addresses must not be published. Configure these in the public Git metadata
before its first commit. The preparation script rejects a personal commit email there.

```powershell
python scripts/prepare_github_source.py --commit
$env:GIT_DIR = (Resolve-Path outputs/checks/github-public.git).Path
$env:GIT_WORK_TREE = (Get-Location).Path
```

Keep these two variables set for the following build, validation and Git publication commands, so the
embedded revision identifies the public commit. In a normal public clone, leave them unset.

## Build, verify and package

```powershell
python scripts/build-package.py
python scripts/prepare_github_release.py
```

The packager refuses a stale executable, mismatched versions, mismatched public source, a dirty application
build, failed Python/frontend tests, unexpected files or a failed extracted-program API smoke test. Tests use
synthetic resources and temporary extraction under `outputs/checks`, then remove the extracted application.
Game battle acceptance remains separate. Logs, the ZIP, SHA256SUMS.txt, release notes and a verification manifest
are in `outputs/checks/github-release`.

If the canonical EXE is open, preserve it, close the editor and rerun the canonical build.

## Publish

Set `$version` to the real version you just validated; do not reuse an existing release tag or overwrite its
assets. The public Git metadata already has its GitHub remote named `origin` after the initial migration.

```powershell
git push origin main
git tag -a "v$version" -m "Cursed Workshop $version"
git push origin "v$version"
gh release create "v$version" --repo Aurus-404/cursed-workshop --verify-tag --draft --title "Cursed Workshop $version" --notes-file outputs/checks/github-release/release-notes.md "outputs/checks/github-release/CursedWorkshop-$version-win64.zip" outputs/checks/github-release/SHA256SUMS.txt outputs/checks/github-release/release-manifest.json
```

Review the draft and its assets, then publish it:

```powershell
gh release edit "v$version" --repo Aurus-404/cursed-workshop --draft=false --latest
```

Verify the public release, direct ZIP URL and SHA-256 after upload. Keep the local logs and publication record.
Do not publish diagnostics or synthetic test workspaces. Finally, in the shared private workspace only, remove
the two environment variables to return Git commands to the private repository:

```powershell
Remove-Item Env:GIT_DIR
Remove-Item Env:GIT_WORK_TREE
```

The build and verification steps are automated by the two Python scripts; publishing remains an explicit
GitHub CLI action. No automatic updater or scheduled publication is installed in the application.
