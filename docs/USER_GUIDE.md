# Cursed Workshop user guide

Cursed Workshop is a work-in-progress, unofficial vehicle editor for Dynamic Conquest in
Call to Arms - Gates of Hell: Ostfront. It is a separate Windows application.

## Install

1. Download the Windows x64 ZIP from https://github.com/Aurus-404/cursed-workshop/releases/latest.
2. Extract the whole archive. Keep CursedWorkshop.exe and its `_internal` folder together.
3. Run `CursedWorkshop/CursedWorkshop.exe`. You do not need Python or Node.js.
4. If the desktop window does not open, install Microsoft's Edge WebView2 Runtime or start
   `CursedWorkshop.exe --browser` to use your default browser.
5. Open Settings and choose your installed game folder if automatic detection does not find it.
   The correct folder contains `resource` and `binaries`. Select additional mod folders if needed.
6. Choose English, Spanish or Simplified Chinese from the language selector.

The old Steam Workshop item is not needed. The application reads resources from your own game and mods;
it does not download them. Install and enable the same DLC/mods that your campaign needs.

## First build

Use a copy of your Conquest campaign. Close the game, or leave it in the main menu with no campaign loaded.
Keep the backups created by the editor.

1. Select and open a campaign.
2. Choose a chassis, an operation and the turret/gun or fittings you want to use.
3. Adjust the design in the 3D view. Save each weapon/turret change when the interface offers that action.
   Saving a pending design in the interface does not replace building the vehicle.
4. Review the preview and validation results, then press Build vehicle and confirm.
5. In the game, open Extras > Mods and enable Cursed Workshop - Built Vehicles last in the mod list.
   Apply the list. After further builds, reload this local mod when the editor asks.
6. Use Check loaded mod in the editor before loading your campaign.

The generated local mod lives in the game's `mods/modular_workshop` folder. The game and campaign must keep
the original DLC/mod dependencies available. Do not remove a donor mod while built vehicles still use it.

## Tools

- Change turrets or individual guns, including a barrel-only option, and mount additional parts.
- Add armour, smoke launchers, machine guns, seats, towing hooks and demolition charges.
- Adjust positions, pivots, rotation/elevation limits and crew visibility.
- Use support-vehicle conversions and the Depot for spare parts.
- Use Creative mode with the Catalog to design vehicles without consuming a campaign donor.
- Offer built vehicles in the Conquest shop or use Storage to remove and later return an owned vehicle.
- Use Update vehicles to rebuild vehicles already present in the open campaign with current fixes.
- Use the armour view to inspect native protection geometry and effective thickness.

Compatibility checks can reject combinations that cannot preserve the game's structural contracts.
A successful preview or export does not guarantee battle behavior for every possible combination.

## Updates, backups and recovery

Close the editor before updating. Extract the new ZIP over the previous program folder or into a new folder.
The application data remains in `%LOCALAPPDATA%/ModularWorkshop` and built vehicles remain in the game's local
mod folder. Keep that data folder, including `vehicle_storage`, when migrating to another PC.
Also preserve your saves, original mod dependencies and generated local mod.

Use Update vehicles after updating if you want existing builds to receive current fixes. Read the release
notes before doing this, and keep the save/mod backups. Do not edit a campaign while the game has it loaded.

## Compatibility and limits

- Supported distribution target: Windows 10/11 x64, Dynamic Conquest.
- A legitimate Gates of Hell installation is required. Optional DLC/mod content must be installed separately.
- The editor reads base-game, DLC and mod resources, including MACE, from the player's installation.
  This does not certify every combination, arbitrary mods or multiplayer.
- Automated tests and synthetic packaged-API checks are distinct from in-game battle acceptance.
- The initial GitHub package's manual battle acceptance is **NOT TESTED**.
- Work in progress: keep backups and try new combinations in a disposable campaign first.

## Reports and feature requests

Use https://github.com/Aurus-404/cursed-workshop/issues. Describe the steps, expected result, actual result,
application version and active game/mod versions. Include screenshots if useful.

Diagnostics can create a local ZIP with saves, logs and built vehicles. Review it before posting it publicly;
you decide what to share. Never include passwords or access tokens. Feature requests should explain the
desired behavior and give a concrete vehicle/gameplay example.
