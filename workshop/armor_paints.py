"""Bundled armor finishes shared by previews and generated game materials."""
from pathlib import Path

PAINTS = {
    'steel': 'Worn steel',
    'british': 'British',
    'british_camouflage': 'British green/black camouflage',
    'finnish_wood': 'Finnish wood',
    'finnish_car': 'Finnish car paint',
    'finnish_tank': 'Finnish tank paint',
    'german_gray_extra': 'German gray extra plates',
    'german_bolted_flak': 'German bolted flak armor',
    'german_desert_zimmerit': 'German desert zimmerit',
    'german_bolted_tank': 'German bolted tank armor',
    'german_desert_extra': 'German desert extra plates',
    'usa_green': 'USA green armor',
    'usa_sherman': 'USA Sherman armor',
    'soviet_green': 'Soviet green armor',
    'soviet_gray': 'Soviet gray armor',
    'french_brown_kaki': 'French brown kaki',
    'french_green_kaki': 'French green kaki',
    'panzergrau': 'Panzergrau',
    'us_olive_drab': 'US green / Sherman Olive drab',
    'russian_4bo': '4BO Russian green',
    'pink': 'Pink',
}

RETIRED = frozenset(('german_bolted_flak', 'german_bolted_tank'))


def texture_bytes(paint):
    return (Path(__file__).parent/'web'/'assets'/'armor-paints'/(paint+'.dds')).read_bytes()


def catalog():
    return [{'id': key, 'label': label} for key, label in PAINTS.items() if key not in RETIRED]
