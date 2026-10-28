"""Check or repair legacy saves using current weapon definitions; dry-run by default."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from workshop.campaign import load_campaign, build_save_copy, repair_weaponry_state
from workshop.compiler import _load_entity
from workshop.resources import ResourceIndex
from workshop.transactions import assert_game_closed
from workshop.weapon_state import weapon_places


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('save', type=Path)
    parser.add_argument('--settings', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Back up and atomically repair this save')
    parser.add_argument('--expect-sha256', help='Refuse to repair a different save revision')
    args = parser.parse_args()
    if args.apply:
        assert_game_closed()
    snapshot = load_campaign(args.save)
    if args.expect_sha256 and snapshot.source_hash != args.expect_sha256:
        raise ValueError('The campaign changed; inspect its current revision before repairing it')
    settings = json.loads(args.settings.read_text('utf-8-sig'))
    index = ResourceIndex(settings['game_root'], settings.get('mod_roots', [])).build()
    try:
        contracts = {}
        for unit in snapshot.units:
            if unit['kind'] == 'vehicle' and unit['template'].startswith('mw_'):
                _, _, root = _load_entity(index, unit['template'])
                contracts[unit['template']] = weapon_places(root)
        replacements, changed = repair_weaponry_state(snapshot, contracts)
    finally:
        index.close()
    result = {'applied': False, 'changed': changed, 'source_sha256': snapshot.source_hash}
    if args.apply and changed:
        raw = build_save_copy(snapshot, replacements)
        operation = args.settings.parent/'operations'/('weapon_state_repair_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'_'+uuid.uuid4().hex[:8])
        operation.mkdir(parents=True)
        backup = operation/args.save.name
        with backup.open('xb') as f:
            f.write(snapshot._zip_bytes)
            f.flush()
            os.fsync(f.fileno())
        path = args.save.resolve()
        temporary = path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            with temporary.open('xb') as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            assert_game_closed()
            if hashlib.sha256(path.read_bytes()).hexdigest() != snapshot.source_hash:
                raise ValueError('The campaign changed during repair; original left untouched')
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        result.update(applied=True, backup=str(backup), output_sha256=hashlib.sha256(raw).hexdigest())
        (operation/'report.json').write_text(json.dumps(result, indent=2), 'utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
