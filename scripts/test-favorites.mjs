import assert from 'node:assert/strict';
import {favorites,toggleFavorite} from '../frontend/favorites.js';
import {uiStorage} from '../frontend/ui-settings.js';
import {translate} from '../frontend/i18n.js';

// Stars are kept per list and the whole set is saved with the preferences.
const saved=[];const save=patch=>{saved.push(structuredClone(patch));};
assert.equal(favorites('armor-asset').size,0);
assert.equal(toggleFavorite('armor-asset','panzer4h#shieldl@body',save),true);
assert.equal(toggleFavorite('cmd-item','mg34',save),true);
assert.equal(toggleFavorite('armor-asset','t26#mantlet@gun_rot',save),true);
assert.deepEqual([...favorites('armor-asset')],['panzer4h#shieldl@body','t26#mantlet@gun_rot']);
assert.ok(!favorites('cmd-item').has('panzer4h#shieldl@body'),'each list keeps its own stars');
assert.equal(toggleFavorite('armor-asset','panzer4h#shieldl@body',save),false,'a second click removes the star');
await new Promise(resolve=>setTimeout(resolve,0));
assert.deepEqual(saved.at(-1),{favorites:{'armor-asset':['t26#mantlet@gun_rot'],'cmd-item':['mg34']}});
toggleFavorite('cmd-item','mg34',save);await new Promise(resolve=>setTimeout(resolve,0));
assert.deepEqual(saved.at(-1).favorites,{'armor-asset':['t26#mantlet@gun_rot']},'an emptied list is dropped');
// A failed write keeps the star for the session.
toggleFavorite('rider-item','dp28',()=>Promise.reject(Error('offline')));await new Promise(resolve=>setTimeout(resolve,0));
assert.ok(favorites('rider-item').has('dp28'));
uiStorage.setItem('cw.favorites','not json');assert.equal(favorites('armor-asset').size,0,'damaged storage reads as no stars');
for(const locale of ['en','zh-CN'])for(const text of ['Favoritos','Mostrar solo favoritos','Marcar como favorito','Quitar de favoritos'])
  assert.notEqual(translate(text,locale),text,text+' '+locale);
console.log('Favorites: per-list stars, toggling, saved set, emptied lists, failed writes, damaged storage and translations passed.');
