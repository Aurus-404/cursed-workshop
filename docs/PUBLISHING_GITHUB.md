# Building and publishing a release

Use Windows x64, Python 3.12.10, Node.js 24 and GitHub CLI. For a fork, use your own repository below.

1. Set the intended version in `workshop/__init__.py`, `package.json`, `package-lock.json` and `pyproject.toml`.
2. Write player-facing changes and installation instructions in `docs/releases/<version>.md`.
3. Install dependencies and rebuild the frontend:

```powershell
npm ci
python -m pip install --target .build-deps -r requirements-build.txt
npm run build
```

Commit the source, then build and validate the Windows package:

```powershell
python scripts/build-package.py
python scripts/prepare_github_release.py
```

Close the existing editor before building. The executable is written to `dist/CursedWorkshop/CursedWorkshop.exe`.
The packaging script checks the committed source, runs tests and exercises the extracted application with
synthetic resources. The ZIP and release notes are in `outputs/checks/github-release`. Verification logs and
manifests stay there for maintainers.

Publish the ZIP with the player-facing release notes:

```powershell
$repository = "YOUR-ACCOUNT/YOUR-REPOSITORY"
$version = (Get-Content package.json -Raw | ConvertFrom-Json).version
git push origin main
git tag -a "v$version" -m "Cursed Workshop $version"
git push origin "v$version"
gh release create "v$version" --repo $repository --verify-tag --draft --title "Cursed Workshop $version" --notes-file outputs/checks/github-release/release-notes.md "outputs/checks/github-release/CursedWorkshop-$version-win64.zip"
```

Review the draft, then publish it:

```powershell
gh release edit "v$version" --repo $repository --draft=false --latest
```

Keep dependency licenses with the download. Review new files for secrets, player data and proprietary assets
before publishing. Do not overwrite an existing release's tag or ZIP; use the next intended version.
