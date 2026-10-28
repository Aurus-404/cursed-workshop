"""Post-battle occupants can be owned through a different campaign squad."""
import tempfile
import unittest
from pathlib import Path

from tests.test_campaign import SCENE, PROFILE, make_save
from workshop.campaign import (CampaignError, load_campaign, plan_edit, plan_refit,
                               plan_assembly, apply_plan, build_save_copy)


class BoardingLinkTests(unittest.TestCase):
    def plans(self, snapshot):
        yield lambda: plan_edit(snapshot, '0x9000', 'mw_boarding', 2, keep_names=True)
        yield lambda: plan_refit(snapshot, '0x9000', 'mw_boarding', 2)
        yield lambda: plan_assembly(snapshot, '0x9000', '0x9010', 'mw_boarding', 2, state_profile=PROFILE)

    def snapshot(self, folder, scene):
        return load_campaign(make_save(Path(folder)/'source.sav', scene))

    def test_other_vehicle_and_reserve_soldiers_dismount_without_squad_or_state_changes(self):
        scene=SCENE.replace('{Link 0x9021 {0x9020 "driver"}}',
                            '{Link 0x9021 {0x9000 "driver2"}}{Link 0x9030 {0x9000 "seat1"}}')
        with tempfile.TemporaryDirectory() as folder:
            snapshot=self.snapshot(folder, scene);original=snapshot.source_path.read_bytes()
            before=snapshot._scene.find('campaign')
            for operation in self.plans(snapshot):
                plan=operation()
                self.assertTrue(any('other squads' in w for w in plan['warnings']))
                destination=Path(folder)/'result.sav'
                destination.write_bytes(build_save_copy(snapshot,apply_plan(snapshot,plan)))
                after=load_campaign(destination)
                result=after._scene.find('campaign')
                # The original crew policy is unchanged; boarded outsiders do
                # not silently become crew members or consume another vehicle.
                self.assertNotIn('0x9021', plan['crew_eids'])
                self.assertNotIn('0x9030', plan['crew_eids'])
                for eid in ('0x9021','0x9030'):
                    old=next(u for u in snapshot.units if u['eid']==eid)
                    new=next(u for u in after.units if u['eid']==eid)
                    self.assertEqual(snapshot.squads[old['squad_index']]['eids'],
                                     after.squads[new['squad_index']]['eids'])
                    old_row=before.find('CampaignSquads').children[old['squad_index']]
                    new_row=result.find('CampaignSquads').children[new['squad_index']]
                    self.assertEqual(old_row.render(),new_row.render())
                for node in before.find_all('Human'):
                    self.assertEqual(next(n for n in result.find_all('Human') if n.args==node.args).render(),node.render())
                for eid in ('0x9020','0x9021','0x9030'):
                    self.assertFalse(any(n.args==[eid] for n in result.find_all('Link')))
                self.assertNotIn('0x9000', {n.children[0].head for n in result.find_all('Link')})
                self.assertEqual(snapshot.source_path.read_bytes(), original)

    def test_attachments_unowned_ambiguous_and_multiple_bindings_remain_blocked(self):
        invalid=[
            '{Link 0x9020 {0x9000 "tow"}}',
            '{Human "mp/ger/mid/tankman" 0x9040}{Link 0x9040 {0x9000 "seat1"}}',
            '{Link 0x9030 {0x9000 "seat1"}}{Link 0x9030 {0x9020 "seat1"}}',
            '{Link 0x9000 {0x9030 "tow"}}',
        ]
        with tempfile.TemporaryDirectory() as folder:
            for extra in invalid:
                snapshot=self.snapshot(folder, SCENE.replace('{CampaignSquads',extra+'{CampaignSquads'))
                for operation in self.plans(snapshot):
                    with self.subTest(extra=extra),self.assertRaisesRegex(CampaignError,'external attachments or unowned occupants'):
                        operation()

    def test_external_occupant_must_belong_to_exactly_one_squad_once(self):
        base=SCENE.replace('{CampaignSquads','{Link 0x9030 {0x9000 "seat1"}}{CampaignSquads')
        variants=[
            base.replace('"" 0x9030}', '"" 0x9030 0x9030}'),
            base.replace('0x9020 0x9021}', '0x9020 0x9021 0x9030}'),
        ]
        with tempfile.TemporaryDirectory() as folder:
            for scene in variants:
                snapshot=self.snapshot(folder,scene)
                for operation in self.plans(snapshot):
                    with self.assertRaisesRegex(CampaignError,'external attachments or unowned occupants'):
                        operation()


if __name__=='__main__':unittest.main()
