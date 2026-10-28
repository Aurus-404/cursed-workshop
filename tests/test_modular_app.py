"""Preparation boundaries: removals and whole donors cannot duplicate depot parts."""
import copy
import unittest
from unittest.mock import patch
import tests.test_app as application


class ModularApplicationTests(unittest.TestCase):
    setUp=application.ApplicationTests.setUp
    tearDown=application.ApplicationTests.tearDown
    _generate_with_fixture_compiler=application.ApplicationTests._generate_with_fixture_compiler

    def prepare(self, transform, *, editing=False, equipped_source=None, catalog=()):
        _,compiler,_,_,_=self._generate_with_fixture_compiler()
        if catalog:self.state.index.has_entity=lambda name:name in catalog
        original=compiler.side_effect
        def compile_fixture(*args):
            result=original(*args)
            result['metadata']['removed_modules']=[equipped_source or {'removed_mount':'turret'}] if transform.get('assembly_draft',{}).get('removals') else []
            result['metadata']['draft_steps']=[]
            return result
        compiler.side_effect=compile_fixture
        source={'eid':'0x100','recipient':'test_hull','donor':'test_donor','recipe':True,'transform':copy.deepcopy(transform)}
        with patch.object(self.state,'edit_source',return_value=source),patch('workshop.campaign.plan_draft',return_value={}) as draft,patch('workshop.campaign.plan_edit',return_value={}) as edit:
            body={'mode':'edit','eid':'0x100'} if editing else {'mode':'design','recipient_eid':'0x100'}
            result=self.state.generate({**body,'transform':transform})
        return result,draft,edit

    def test_removal_only_prepares_one_piece_and_needs_no_donor(self):
        result,draft,edit=self.prepare({'assembly_draft':{'schema':1,'removals':[{'mount':'turret'}],'turrets':[],'guns':[]}})
        self.assertEqual(draft.call_args.args[1],'0x100');edit.assert_not_called()
        parts=result['depot_change']['add']
        self.assertEqual([(p['part'],p['template'],p['mount']) for p in parts],[('turret','test_hull','turret')])

    def test_edit_does_not_deposit_the_same_removed_piece_twice(self):
        result,draft,edit=self.prepare({'assembly_draft':{'schema':1,'removals':[{'mount':'turret'}],'turrets':[],'guns':[]}},editing=True)
        draft.assert_not_called();self.assertEqual(edit.call_count,1)
        self.assertEqual(result['depot_change']['add'],[])

    def test_complete_donor_leaves_no_duplicate_chassis_in_depot(self):
        tr={'assembly_draft':{'schema':1,'removals':[{'mount':'turret'}],
            'turrets':[{'donor_template':'test_donor','donor_eid':'0x200','recipient_mount':'@free','assembly_scope':'complete'}],'guns':[]}}
        result,_,_=self.prepare(tr)
        self.assertEqual([p['part'] for p in result['depot_change']['add']],['turret'])
        self.assertEqual(result['depot_change']['take'],[])

    def test_catalog_turret_spends_no_unit_and_must_exist(self):
        row={'donor_template':'test_donor','donor_catalog':True,'recipient_mount':'turret'}
        result,draft,_=self.prepare({'assembly_draft':{'schema':1,'turrets':[row],'guns':[]}},catalog=('test_donor',))
        self.assertEqual(draft.call_args.args[1],'0x100')
        self.assertEqual([p['part'] for p in result['depot_change']['add']],['turret'])
        self.assertEqual(result['depot_change']['take'],[])
        with self.assertRaisesRegex(ValueError,'catálogo'):
            self.prepare({'assembly_draft':{'schema':1,'turrets':[{**row,'donor_template':'missing_vehicle'}],'guns':[]}},catalog=('test_donor',))

    def test_equipped_depot_entry_uses_exported_source_and_exact_mount(self):
        result,_,_=self.prepare({'assembly_draft':{'schema':1,'removals':[{'mount':'turret'}],'turrets':[],'guns':[]}},
            equipped_source={'removed_mount':'aux1','template':'mw_equipped_parts','source_mount':'aux1'})
        entry=result['depot_change']['add'][0]
        self.assertEqual((entry['template'],entry['mount']),('mw_equipped_parts','aux1'))

    def test_native_module_edit_does_not_need_a_second_vehicle_or_deposit_parts(self):
        result,draft,edit=self.prepare({'assembly_draft':{'schema':1,'turrets':[{'native':True,'recipient_mount':'turret',
            'pose':{'position':[1,0,0],'rotation':[0,0,0]}}],'guns':[]}})
        self.assertEqual(draft.call_args.args[1],'0x100');edit.assert_not_called()
        self.assertEqual(result['depot_change'],{'add':[],'take':[]})


if __name__=='__main__':unittest.main()
