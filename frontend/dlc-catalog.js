import {t} from './i18n.js';

export function dlcCatalogNote(catalog){
  return t(catalog?.dlc?.status==='last_game_run'
    ? 'Incluye armas de los DLC adquiridos e instalados que reconoció el juego en su última ejecución.'
    : 'Para incluir armas DLC, abrí el juego con tus DLC instalados y volvé a leer los recursos. Por ahora se muestran armas del juego base.');
}
