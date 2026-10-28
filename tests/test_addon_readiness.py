import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workshop.addon_readiness import parse_load, publication_fence, readiness


class AddonReadinessTests(unittest.TestCase):
    def test_discarded_workshop_roster_is_not_reported_as_loaded(self):
        discarded='Loading Campaign: example\nDiscard mw_example: Multiplayer unit not found during campaign load (eCampaignSquads.cpp:634)\n'
        for activation in (self.local,self.local+'Mod: activate mod other_mod\n'):
            with self.subTest(activation=activation):
                log=self.mount+activation+self.page+discarded
                self.assertEqual(parse_load(log,self.root),'roster_missing')
                self.log.write_text(log,encoding='utf8')
                result=readiness(self.root,{'running':True},installed=True,log=self.log)
                self.assertFalse(result['ready'])
                self.assertIn('No guardes',result['message'])
                self.assertEqual(parse_load(log+self.local+self.page,self.root),'loaded')
        reserve=self.mount+self.local+self.page+'Discard mw_reserve_example(ger): Multiplayer unit not found during campaign load\n'
        self.assertEqual(parse_load(reserve,self.root),'roster_missing')

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)/'game'
        self.log=Path(self.temp.name)/'game.log'
        self.mount=f'Mount package {self.root.as_posix()}/resource/entity.pak with 100 entries\n'
        self.local='Mod: activate mod modular_workshop\n'
        self.page='GUI: Show page page_main with mods [ 1 unique]\n'

    def tearDown(self):self.temp.cleanup()

    def test_report_sequence_disables_local_and_only_activates_steam_tool(self):
        log=self.mount+self.local+self.page+'Mod: disable mod modular_workshop\nMod: activate mod mod_3823214707\n'+self.page
        self.assertEqual(parse_load(log,self.root),'missing')

    def test_incomplete_reload_or_later_roster_override_is_not_ready(self):
        self.assertEqual(parse_load(self.mount+self.local,self.root),'reload_required')
        log=self.mount+self.local+'Mod: activate mod other_mod\n'+self.page
        self.assertEqual(parse_load(log,self.root),'reload_required')
        self.log.write_bytes(log.encode('utf-8'))
        state=readiness(self.root,{'running':True},installed=True,log=self.log)
        self.assertFalse(state['ready'])
        self.assertIn('al final de la lista',state['message'])
        self.assertEqual(parse_load(log+self.local+self.page,self.root),'loaded')

    def test_publication_requires_a_new_activation_not_just_log_activity(self):
        before=self.mount+self.local+self.page
        self.log.write_bytes(before.encode('utf-8'))
        fence=publication_fence(self.root,self.log)
        self.assertEqual(parse_load(before+'Heartbeat\n'+self.page,self.root,fence),'reload_required')
        after=before+'Mod: disable mod modular_workshop\n'+self.local+self.page
        self.assertEqual(parse_load(after,self.root,fence),'loaded')

    def test_replaced_log_must_be_newer_and_match_installation(self):
        self.log.write_text(self.mount+'old session\n',encoding='utf-8')
        fence=publication_fence(self.root,self.log)
        raw=self.mount+self.local+self.page
        self.assertEqual(parse_load(raw,self.root,fence,modified_ns=fence['time_ns']-1),'unknown')
        self.assertEqual(parse_load(raw,self.root,fence,modified_ns=fence['time_ns']+1),'loaded')
        self.assertEqual(parse_load(raw,self.root/'other',fence,modified_ns=fence['time_ns']+1),'unknown')

    def test_closed_game_missing_addon_or_unreadable_log_never_claim_loaded(self):
        self.log.write_text(self.mount+self.local+self.page,encoding='utf-8')
        self.assertFalse(readiness(self.root,{'running':False},installed=True,log=self.log)['ready'])
        self.assertFalse(readiness(self.root,{'running':True},installed=False,log=self.log)['ready'])
        self.assertFalse(readiness(self.root,{'running':True},installed=True,log=self.log.with_suffix('.missing'))['ready'])
        self.assertTrue(readiness(self.root,{'running':True},installed=True,log=self.log)['ready'])

    def test_absent_log_before_publication_rejects_old_log(self):
        fence=publication_fence(self.root,self.log)
        raw=self.mount+self.local+self.page
        self.assertEqual(parse_load(raw,self.root,fence,modified_ns=fence['time_ns']-1),'unknown')

    def test_interrupted_publication_never_accepts_a_new_activation(self):
        fence=publication_fence(self.root,self.log)
        fence['pending']=True
        self.assertEqual(parse_load(self.mount+self.local+self.page,self.root,fence,modified_ns=fence['time_ns']+1),'publication_pending')
        state=readiness(self.root,{'running':False},installed=True,fence=fence,log=self.log)
        self.assertEqual(state['status'],'publication_pending')
        self.assertIn('repetí la instalación',state['message'])

    def test_no_required_addon_does_not_display_a_warning(self):
        self.assertFalse(readiness(self.root,{},installed=False,required=False)['required'])

    def test_publication_fence_is_persisted_and_reused_after_restart(self):
        from workshop.app import AppState
        self.log.write_text(self.mount+self.local+self.page,encoding='utf-8')
        workspace=Path(self.temp.name)/'workspace'
        state=AppState(workspace)
        try:
            state.settings['game_root']=str(self.root)
            with patch('workshop.addon_readiness.game_log_path',return_value=self.log):state.mark_addon_published()
        finally:state.pool.shutdown(wait=True)
        restarted=AppState(workspace)
        try:
            self.assertEqual(parse_load(self.log.read_bytes(),self.root,restarted.settings['addon_publication']),'reload_required')
        finally:restarted.pool.shutdown(wait=True)

    def test_recovery_copy_survives_changes_to_live_save_and_rejects_bad_content(self):
        import hashlib
        from workshop.addon_readiness import preserve_campaign
        source=Path(self.temp.name)/'campaign.sav';source.write_bytes(b'original')
        staged=Path(self.temp.name)/'prepared.sav';staged.write_bytes(b'assembled')
        digest=hashlib.sha256(staged.read_bytes()).hexdigest()
        backup=preserve_campaign(Path(self.temp.name)/'workspace',source,staged,digest)
        source.write_bytes(b'game discarded vehicle')
        self.assertEqual(Path(backup).read_bytes(),b'assembled')
        self.assertNotIn('operations',Path(backup).parts)
        self.assertEqual(preserve_campaign(Path(self.temp.name)/'workspace',source,staged,digest),backup)
        with self.assertRaises(ValueError):preserve_campaign(self.root,source,staged,'wrong hash')
        Path(backup).write_bytes(b'external change')
        with self.assertRaises(ValueError):preserve_campaign(Path(self.temp.name)/'workspace',source,staged,digest)
        self.assertEqual(Path(backup).read_bytes(),b'external change')

    def test_live_profile_is_untouched_but_closed_game_activation_is_prepared(self):
        from workshop.app import AppState
        from workshop.profile import active_mods
        profile=Path(self.temp.name)/'profile';(profile/'campaign').mkdir(parents=True)
        options=profile/'options.set';options.write_text('{options {mods "mod_3823214707:0"}}','utf-8')
        state=AppState(Path(self.temp.name)/'workspace')
        try:
            before=options.read_bytes()
            self.assertIsNone(state.prepare_addon_activation(profile/'campaign/test.sav',{'running':True}))
            self.assertEqual(options.read_bytes(),before)
            result=state.prepare_addon_activation(profile/'campaign/test.sav',{'running':False})
            self.assertTrue(result['changed'])
            self.assertEqual(active_mods(options),['mod_3823214707:0','modular_workshop:0'])
            self.assertEqual(Path(result['backup']).read_bytes(),before)
        finally:state.pool.shutdown(wait=True)
