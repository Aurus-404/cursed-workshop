import unittest
from types import SimpleNamespace

from workshop.point_fittings import charge_contract,add_charge_interactions,PointFittingError
from workshop.syntax import block
from tests.test_compiler import FakeResources


class ChargeContractTests(unittest.TestCase):
    def resources(self):
        r=FakeResources()
        r.read_text=lambda p:r.read(p).decode()
        r.resolve=lambda p:SimpleNamespace(as_dict=lambda:{'path':p},source_root='unused-fixture-source')
        r.verify_unchanged=lambda p:None
        r.files['set/interaction_entity/entity.set']=b'{entities {"native_charge" {on fire "payload" {components "body" destroy}{delay 0.1 {delete}}}} {"small_source" {on "explosion_blastwave" {blastwave c4 45 r0 2.6 r1 5.2}}} {"large_source" {on "explosion_blastwave" {blastwave c4 65 r0 3 r1 6}}}}'
        for name,size in [('small_source','60kg'),('large_source','100kg')]:
            r.files[f'entity/{name}/{name}.def']=('{game_entity {props "native_charge"}{Weaponry {place "payload" {weapon "weapon goliath" filling "ammo goliath '+size+'" 1}}}}').encode()
        return r

    def test_native_blast_authority_follows_the_selected_filling(self):
        r=self.resources()
        for size,template,power in [('60kg','small_source','45'),('100kg','large_source','65')]:
            native=charge_contract(r,'ammo goliath '+size)
            self.assertEqual(native['native_template'],template)
            record=block('{"assembled tank" {on fire "gun" {call "recoil"}} {on break {call "chassis_break"}} {on "explosion_blastwave" {blastwave c4 3 r0 3 r1 8}}}')
            result=add_charge_interactions(record,{'charge':size},r)
            fire=next(n for n in result.find_all('on') if n.args[:2]==['fire','cw_explosive'])
            self.assertIn('explosion',[n.args[0] for n in fire.walk('call')])
            self.assertTrue(list(fire.walk('components')))
            wave=next(n for n in result.find_all('on') if n.args[:1]==['explosion_blastwave'])
            self.assertEqual([n.args[1] for n in wave.walk('blastwave')],[power,'3'])
            self.assertIn('chassis_break',result.render())
            self.assertIn('recoil',result.render())
            again=add_charge_interactions(result,{'charge':size},r)
            self.assertEqual(len([n for n in again.find_all('on') if n.args[:2]==['fire','cw_explosive']]),1)
            self.assertEqual(len([n for n in again.find_all('on') if n.args[:1]==['explosion_blastwave']]),1)
            other=add_charge_interactions(again,{'charge':'100kg' if size=='60kg' else '60kg'},r)
            wave=next(n for n in other.find_all('on') if n.args[:1]==['explosion_blastwave'])
            self.assertEqual([n.args[1] for n in wave.walk('blastwave')],['65' if size=='60kg' else '45','3'])

    def test_missing_native_detonation_contract_fails_before_export(self):
        r=self.resources();r.files.pop('entity/small_source/small_source.def')
        with self.assertRaises(PointFittingError):charge_contract(r,'ammo goliath 60kg')
