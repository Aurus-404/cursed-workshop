# Distribution and source review

Cursed Workshop is an independent file editor. The application reads installed game/mod resources,
assembles a local mod and updates the selected Conquest save and associated profile settings using backups
and transactions. Players must supply their own legitimate game installation and any required mods/DLC.

The application's HTTP server binds to `127.0.0.1`. API mutations require a local session token and validate
Host/Origin. The process guard enumerates process names and queries executable paths using
`PROCESS_QUERY_LIMITED_INFORMATION`; it does not read or write the game's process memory. No DLL injector,
remote-memory patcher or game-executable patching implementation was found in the application source reviewed
for the initial GitHub release. The desktop application loads its own Python/.NET/WebView2 dependencies.
Those libraries are application dependencies, not DLLs injected into the game.

Diagnostics are generated locally; users decide whether and where to share them. The application source
does not implement an automatic report upload or application updater. These are bounded source-review
findings, not a security certification or a promise about all operating-system/runtime behavior.

## Public source scope

`scripts/prepare_github_source.py` lists the public files explicitly: the complete application and frontend,
compiled frontend, build dependencies, build/release tools, compatibility tools, synthetic tests and public
documentation. Internal handoffs, player attachments/reports, real saves, extracted game/mod files, reverse
engineering experiments, local diagnostics, personal lab scripts and the private Git history are excluded.
The existing game screenshot used as the Steam thumbnail is also excluded from the GitHub source/package.
The application icon is drawn by the build script.

The public source scanner rejects known credential formats, private keys and contact candidates for review.
The package scanner rejects game assets, saves, settings, debug maps, development folders and unexpected
archives. Scans supplement manual review; they cannot prove the absence of every possible secret or defect.

## Licenses

No open-source license has been selected for Cursed Workshop. Publishing its source for inspection does not
grant a general permission to modify or redistribute it. Contact the repository owner about permissions.
Do not add a project license without the owner's decision.

Third-party runtime/build components retain their own licenses. Original notices, dependency versions and
Python's bundled-component notices are included in `THIRD_PARTY_LICENSES` inside the downloadable ZIP.
See `docs/third_party/README.md` for supplemental notice origins. PyInstaller's license includes its bootloader
exception; its notices are included. No proprietary game or mod assets are included in the GitHub download.
