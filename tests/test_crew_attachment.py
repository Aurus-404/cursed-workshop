import unittest,tempfile
from pathlib import Path
from workshop.syntax import parse,block
from workshop.geometry import parse_model,point,identity
from workshop.crew_attachment import apply_crew_fixed
from workshop.compiler import CompileError,compile_assembly
from tests.test_compiler import FakeResources

class CrewAttachmentTests(unittest.TestCase):
 def setup_model(self):
  root=parse('{game_entity {placer {place "gunner" {group "crew"}{LinkBone "gun"}}{place "charger" {group "crew"}{LinkBone "loader"}}}{Weaponry {place "gun" {LinkBone "gun"}{gunner "gunner"}{charger "charger"}}}}').find('game_entity')
  model=parse_model('{skeleton {bone basis {bone body {bone revolute turret {Position 2 3 4}{bone gun {Position 1 0 0}{VolumeView "gun.ply"}}{bone loader {Position -3 0 0}}}}}}')
  return root,model
 def test_fixing_shared_weapon_bone_only_moves_actor_attachment_and_preserves_reflection(self):
  root,model=self.setup_model();before=model['bones']['gun']['node'].render();world=model['bones']['gun']['world']
  with_reflection=model['bones']['gun']['node'];with_reflection.append(block('{orientation 1 0 0 0 -1 0 0 0 1}'))
  model=parse_model(model['document'].render());world=model['bones']['gun']['world'];before=model['bones']['gun']['node'].render()
  report=apply_crew_fixed(FakeResources(),root,model,{'gunner':True,'charger':False})
  self.assertEqual(model['bones']['gun']['node'].find('VolumeView').args,['gun.ply'])
  self.assertEqual(model['bones']['gun']['world'],world)
  self.assertEqual(model['bones']['gun']['parent'],'turret')
  self.assertEqual(model['bones']['cw_crew_fixed_gunner']['parent'],'body')
  self.assertEqual(model['bones']['cw_crew_fixed_gunner']['world'],world)
  self.assertEqual(model['bones']['cw_crew_follow_charger']['parent'],'loader')
  self.assertEqual(root.find('Weaponry').find('place').find('LinkBone').args,['gun'])
  self.assertEqual(root.find('Weaponry').find('place').find('gunner').args,['gunner'])
  self.assertEqual([r['fixed'] for r in report],[True,False])
 def test_switching_back_to_following_keeps_world_pose_and_does_not_duplicate_anchors(self):
  root,model=self.setup_model();apply_crew_fixed(FakeResources(),root,model,{'gunner':True})
  world=model['bones']['cw_crew_fixed_gunner']['world']
  for state in [False,True,False]:apply_crew_fixed(FakeResources(),root,model,{'gunner':state})
  self.assertEqual(model['bones']['cw_crew_follow_gunner']['world'],world)
  self.assertEqual(model['bones']['cw_crew_follow_gunner']['parent'],'gun')
  self.assertEqual(len([n for n in model['document'].walk('bone') if n.args==['cw_crew_follow_gunner']]),1)
 def test_invalid_internal_and_unknown_roles_are_rejected(self):
  root,model=self.setup_model()
  for value in ({'gunner':1},{'unknown':True},{1:True},[]):
   with self.subTest(value=value),self.assertRaises(CompileError):apply_crew_fixed(FakeResources(),root,model,value)
  root.find('placer').append(block('{place "driver" {group "crew"}{LinkBone ""}}'))
  with self.assertRaisesRegex(CompileError,'interno'):apply_crew_fixed(FakeResources(),root,model,{'driver':True})
 def test_independent_pins_keep_seats_and_hidden_crew_operators(self):
  from workshop.crew_visibility import apply_visibility
  root,model=self.setup_model()
  root.append(block('{boarder {link "door" "charger" {forward putoff}{reverse puton}}}'))
  apply_crew_fixed(FakeResources(),root,model,{'gunner':False,'charger':True})
  self.assertEqual(model['bones']['cw_crew_follow_gunner']['parent'],'gun')
  self.assertEqual(model['bones']['cw_crew_fixed_charger']['parent'],'body')
  apply_visibility(root,['charger'],resources=FakeResources())
  links=[p.find('LinkBone').args for pl in root.find_all('placer') for p in pl.find_all('place') if p.args==['charger']]
  self.assertTrue(links);self.assertTrue(all(link==[''] for link in links))
  self.assertEqual(root.find('Weaponry').find('place').find('charger').args,['charger'])
 def test_complete_export_preserves_choice_and_keeps_weapon_geometry(self):
  resources=FakeResources();key='entity/hull/hull.def'
  resources.files[key]=resources.files[key].replace(b'{mass 9000}',b'{placer {place "gunner" {group "crew"}{LinkBone "gun"}}}{mass 9000}')
  with tempfile.TemporaryDirectory() as temp:
   result=compile_assembly(resources,'hull','hull',{'commander_only':True,'crew_fixed':{'gunner':True}},temp,'mw_crew_fixed')
   model=parse_model((Path(temp)/'resource'/result['model_path']).read_bytes())
   self.assertEqual(model['bones']['cw_crew_fixed_gunner']['parent'],'body')
   self.assertEqual(model['bones']['gun']['parent'],'turret')
   self.assertEqual(result['metadata']['transform']['crew_fixed'],{'gunner':True})

if __name__=='__main__':unittest.main()
