import assert from 'node:assert/strict';
import {dlcCatalogNote} from '../frontend/dlc-catalog.js';
globalThis.localStorage={getItem:()=> 'es'};
assert.match(dlcCatalogNote({dlc:{status:'last_game_run'}}),/última ejecución/);
assert.match(dlcCatalogNote({dlc:{status:'unknown'}}),/volvé a leer/);
assert.match(dlcCatalogNote({}),/juego base/);
console.log('DLC catalog notes distinguish the last game scan from unknown availability.');
