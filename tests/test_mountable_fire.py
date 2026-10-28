import unittest
from workshop.syntax import parse
from workshop.commander_mg import add_mountable_fire,add_interactions
from workshop.compiler import CompileError

class MountableFireTests(unittest.TestCase):
 def test_generated_slots_dispatch_native_inventory_weapon_effects_once(self):
  root=parse('{vehicle {on fire "gun" {view start "cannon_flash"}}}').children[0]
  original=root.find('on').render()
  for slot in ('mgun_cmd','mgun_cmd2','mgun_rider1'):
   add_mountable_fire(root,slot);add_mountable_fire(root,slot)
   event=next(n for n in root.find_all('on') if n.args==['fire',slot])
   self.assertEqual(len(list(event.walk('call'))),1)
   self.assertEqual(event.find('if').args,['effector','stuff weapon'])
   self.assertEqual(event.find('if').find('with_effector').find('call').args,['fire'])
   self.assertEqual(list(event.walk('view')),[])
  self.assertEqual(root.find('on').render(),original)
 def test_commander_hatch_and_existing_handler_are_kept(self):
  root=parse('{vehicle {on fire "mgun_cmd" {tags add "observed"}}{on commander_hatch_open {view show hatch}}}').children[0]
  add_interactions(root,{'slot':'mgun_cmd','sequence':'cmdmg_pose'})
  self.assertIn('{tags add "observed"}',root.render())
  self.assertEqual(len(list(root.walk('call'))),1)
  self.assertIn('{view show hatch}',root.render())
  self.assertEqual(len(list(root.walk('ani_play'))),2)
 def test_duplicate_explicit_handlers_are_rejected(self):
  root=parse('{vehicle {on fire "slot"}{on fire "slot"}}').children[0]
  with self.assertRaises(CompileError):add_mountable_fire(root,'slot')

if __name__=='__main__':unittest.main()
