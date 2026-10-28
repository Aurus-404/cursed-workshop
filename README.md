# Cursed Workshop

**An external vehicle workshop for _Call to Arms – Gates of Hell: Ostfront_ Dynamic Conquest.**

Cursed Workshop lets you rebuild the vehicles of your Conquest campaign: swap turrets and guns between vehicles,
mount extra turrets and weapons, add armour and equipment, and play the result in your own campaign. It is a
standalone Windows program that reads the game's installed resources and writes a regular local mod plus the
campaign save you choose.

> **Work in progress.** Expect bugs. Try it on a copy of your campaign first, and keep the backups it makes.
>
> **Unofficial community project.** Cursed Workshop is not made, endorsed or supported by Barbed Wire Studios or
> by any game publisher.

## Features

- **Turret swaps** between compatible vehicles of your campaign, from vanilla, DLC or mods such as MACE.
- **Gun swaps**: replace a single gun and keep the rest of the turret, including guns taken from towed artillery
  and emplacements ("barrel only" keeps just the tube).
- **Vehicle design**: several turret and gun changes on one vehicle, extra turrets mounted freely on the hull or
  on another turret, and turrets removed to the depot.
- **Fittings**: extra armour (tracks, sandbags, plates, skirts, wood), smoke launchers, commander and passenger
  machine guns, passenger seats, towing hooks and demolition charges.
- **Support vehicles**: turn a hull into an ammunition, engineering or fuel supply vehicle.
- **Crew control**: seat positions, who rides along, crew hidden while boarded; guns bring the loaders they need.
- **Creative mode**: build from any vehicle in the catalog, either onto a vehicle of your campaign or for the
  Conquest shop, where it is sold at the price of its parts.
- **Depot and storage**: leftover parts go to a depot; whole vehicles can be stored and moved between campaigns.
- **Armour view**: the game's armour volumes over the vehicle, coloured by thickness, with effective thickness
  at your viewing angle.
- **Update vehicles**: rebuild the vehicles you already made with the latest fixes.
- **Diagnostics**: one button makes a report `.zip` for bug reports.
- Interface in **English, Spanish and Simplified Chinese**.

## Download

Get the latest version from the **[Releases page](https://github.com/Aurus-404/cursed-workshop/releases/latest)**:
download `CursedWorkshop-<version>-win64.zip`.

Each release lists the SHA-256 of the `.zip` (also in `SHA256SUMS.txt`). To check your download in PowerShell:

```powershell
Get-FileHash .\CursedWorkshop-<version>-win64.zip -Algorithm SHA256
```

## Requirements

- Windows 10 or 11, 64-bit.
- _Call to Arms – Gates of Hell: Ostfront_ installed through Steam.
- Microsoft Edge WebView2 Runtime (already present on Windows 11 and on most Windows 10 PCs; if the window does
  not open, install it from Microsoft or run the program with `--browser`).

## Installation

1. Extract the `.zip` anywhere you like, for example `Documents\CursedWorkshop`.
2. Run `CursedWorkshop\CursedWorkshop.exe`.
3. The game folder is found automatically from your Steam libraries. If it is not, open **Settings** and choose
   the `Call to Arms - Gates of Hell` folder (the one that contains `resource` and `binaries`).

Nothing is installed system-wide. Backups and operation records are kept in `%LOCALAPPDATA%\ModularWorkshop`.
To update, extract the new release over the old folder or into a new one; your built vehicles and backups stay.

**Coming from the Steam Workshop version?** Your built vehicles live in the game's `mods\modular_workshop`
folder and keep working. Install this version and press **Update vehicles** once.

## Using it with Gates of Hell

1. Close the game, or leave it in the main menu with no campaign loaded.
2. In Cursed Workshop, open your Conquest campaign.
3. Pick a chassis and a turret (or a gun, or a tool such as extra armour), adjust it in the 3D view and press
   **Build vehicle**. The tool shows what will change and asks for confirmation; your save is backed up first.
4. In the game, go to **Extras > Mods**, enable **Cursed Workshop - Built Vehicles** last in the list and apply.
5. Use **Check loaded mod** in the tool before opening the campaign.

[User guide and compatibility](docs/USER_GUIDE.md) · [Release notes](docs/releases/0.8.9.md)

## What the program does on your PC

These points describe the current source code; check them yourself in this repository.

- Its main outputs are its local mod (`mods\modular_workshop` in the game folder), the campaign save you choose
  (after making a backup), related game-profile settings and its data folder in `%LOCALAPPDATA%\ModularWorkshop`.
  Diagnostic exports and screenshots are saved to locations you choose.
- It reads the game's installed resources and mods to build vehicles. No game or mod files are included in the
  download.
- It checks game processes and the game log to block campaign changes while a battle or campaign is loaded.
- Its interface is a local web page served on `127.0.0.1` only.
- No DLL injection, game-executable patching or game-memory modification was found in the reviewed application
  code. Diagnostic reports are generated locally and shared only by your choice.

The executable is built with PyInstaller. Its SHA-256 identifies the published download; it is not a security
certification. You can inspect and build the source yourself (see below).
[Review scope and distribution notices](docs/DISTRIBUTION.md).

## Reporting bugs

Open an **[issue](https://github.com/Aurus-404/cursed-workshop/issues)** and include:

- what you did, what you expected and what happened (screenshots help);
- the diagnostic report: **Diagnostics > Generate diagnostic .zip** in the tool.

The diagnostic `.zip` contains your campaign save, the game and tool logs and your built vehicles. Look at it
before sharing it publicly if that matters to you.

## Suggesting features

Open an **[issue](https://github.com/Aurus-404/cursed-workshop/issues)** describing the vehicle, the change
you want and, if you can, a historical or in-game example.

## Why is Cursed Workshop now distributed through GitHub?

Cursed Workshop was first distributed through the Steam Workshop of Gates of Hell. Barbed Wire Studios has
announced Workshop security-policy changes concerning DLL injection and related executable code
([Important Workshop announcement](https://store.steampowered.com/news/app/400750/view/715668461242548712)),
and our Workshop item is no longer available. Cursed Workshop is a standalone editor, so we now publish it here,
with its source code and versioned downloads, so players can keep using it and review how it works.

## Building from source

Requirements: Windows x64, Python 3.12.10 and Node.js 24.

```powershell
npm ci
python -m pip install --target .build-deps -r requirements-build.txt
npm run build
python scripts/build-package.py
```

The program is written to `dist\CursedWorkshop\CursedWorkshop.exe`. Tests: `python -m unittest discover -s tests`
and the `scripts\test-*.mjs` files with Node. Maintainers publish releases as described in
[docs/PUBLISHING_GITHUB.md](docs/PUBLISHING_GITHUB.md). Python dependency versions are pinned in
`requirements-build.txt`; the generated package includes the licenses of its runtime dependencies.

## License

No open-source license has been chosen for this project yet. Third-party components keep their own licenses,
listed in [docs/third_party](docs/third_party) and in `THIRD_PARTY_LICENSES` inside each release.
