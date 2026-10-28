"""Exercise the packaged HTTP API using only a synthetic, isolated workspace."""
import argparse
import hashlib
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from tests.test_gun_assembly import Resources


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--crew-visibility',action='store_true',help='Also exercise internal crew compilation.')
    parser.add_argument('--support-vehicle',action='store_true',help='Also exercise native support packages and cargo.')
    parser.add_argument('--extra-armor',action='store_true',help='Also exercise physical native armor fittings.')
    parser.add_argument('--smoke-launchers',action='store_true',help='Exercise original smoke models and native dispatch compilation.')
    parser.add_argument('--corrections',action='store_true',help='Exercise artwork, cancellation and the operation panel.')
    args=parser.parse_args();out=args.out.resolve()
    if not out.is_relative_to((ROOT/'outputs').resolve()) or out.exists():raise ValueError('Use a new output folder.')
    out.mkdir(parents=True);game=out/'fixture_game';(game/'binaries').mkdir(parents=True)
    fixture=Resources()
    fixture.files['set/stuff/shell/mg_round.belt.ammo']=b'{ammo {entity "mg_bullet"}}'
    if args.smoke_launchers:
        from tests.test_smoke_launchers import resources as smoke_resources
        extra=smoke_resources()
        fixture.files.update({p:v for p,v in extra.files.items() if p.startswith('set/interaction_entity/tank/')
            or 'nbks.grenade' in p or 'nbk39/' in p or 'flash_grenade_launcher.def' in p})
    if args.extra_armor:
        fixture.files['properties/vehicle.ext']=b'{props "steel"}(define "armor" {volume %0 {thickness %1}{tags "armor"}})'
        prefix='entity/-vehicle/test/plate/'
        fixture.files[prefix+'plate.def']=b'{game_entity (include "/properties/vehicle.ext"){extension "plate.mdl"}("armor" args "panel" 12)}'
        fixture.files[prefix+'plate.mdl']=b'{skeleton {bone "body" {bone "plate" {VolumeView "plate.ply"}}}}{volume "panel" {bone "plate"}{box 2 3 1}}'
        fixture.files[prefix+'plate.ply']=fixture.files['entity/receiver/hull.ply']
    if args.support_vehicle:
        fixture.files['properties/resupply.inc']=b'(define "native_support" {extender "supply_zone" {limit 250}{radius 10}{resupply {item "native_round" "ammo" 10}}})'
        fixture.files['entity/-vehicle/test/support/support.def']=b'{game_entity (include "/properties/resupply.inc") ("native_support")}'
        folder='entity/construction/_military/boxes/test_crate/'
        fixture.files[folder+'test_crate.def']=b'{game_entity {model "test_crate"}}'
        fixture.files[folder+'test_crate.mdl']=b'{skeleton {bone "body" {VolumeView "crate.ply"}}}'
        fixture.files[folder+'crate.ply']=fixture.files['entity/receiver/hull.ply']
    if args.crew_visibility:
        from workshop.syntax import parse,block
        path='entity/receiver/receiver.def';doc=parse(fixture.files[path]);root=doc.find('game_entity')
        seat=next(p for pl in root.find_all('placer') for p in pl.find_all('place') if p.args==['gunner'])
        root.append(block('(define "visual_wrapper" {placer {place "gunner" {LinkBone "coax"}}})'))
        root.append(block('("visual_wrapper")'))
        root.append(block('{boarder {door "emit"}{link "emit" "gunner" {forward putoff}{reverse puton}}}'))
        fixture.files[path]=doc.render().encode('utf8')
    if args.corrections:
        from workshop.icons import write_tga
        fixture.files['interface/scene/unit_icon/receiver_00.tga']=write_tga(1,1,bytes([60,80,50,255]))
    for path,raw in fixture.files.items():
        target=game/'resource'/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
    with zipfile.ZipFile(game/'resource/gamelogic.pak','w'):pass
    workspace=out/'workspace';workspace.mkdir()
    (workspace/'settings.json').write_text(json.dumps({'game_root':str(game),'mod_roots':[]}),encoding='utf8')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',0));port=probe.getsockname()[1]
    url=f'http://127.0.0.1:{port}'
    def request(path,body=None,token=None):
        headers={'Content-Type':'application/json'}
        if token:headers['X-Workshop-Token']=token
        req=urllib.request.Request(url+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
        with urllib.request.urlopen(req,timeout=5) as response:return response.read()
    process=subprocess.Popen([str(args.exe.resolve()),'--no-browser','--port',str(port),'--workspace',str(workspace)],
                             cwd=args.exe.resolve().parent,creationflags=subprocess.CREATE_NO_WINDOW)
    report={'runtime_verified':False,'game_opened':False,'personal_saves_touched':False};token=None
    try:
        for _ in range(40):
            try:token=json.loads(request('/api/session'))['token'];break
            except OSError:
                if process.poll() is not None:raise RuntimeError('Packaged process stopped before startup')
                time.sleep(.25)
        if not token:raise RuntimeError('Packaged HTTP startup timed out')
        config=json.loads(request('/api/config',token=token))
        if Path(config['workspace']).resolve()!=workspace or process.poll() is not None:
            token=None  # Never configure or shut down a process we did not launch.
            raise RuntimeError('HTTP endpoint does not belong to the isolated test process')
        def job(path,body):
            key=json.loads(request(path,body,token))['job_id']
            for _ in range(200):
                status=json.loads(request('/api/job?id='+key,token=token))
                if status['status']=='error':raise AssertionError(status['error'])
                if status['status']=='done':return status['result']
                time.sleep(.05)
            raise RuntimeError('Fixture job timed out')
        try:
            request('/api/gun-catalog',{'template':'receiver'})
            raise AssertionError('Gun API accepted missing token')
        except urllib.error.HTTPError as error:assert error.code==403
        job('/api/configure',{'game_root':str(game),'mod_roots':[]})
        if args.corrections:
            diagnostic=json.loads(request('/api/diagnostic',token=token))
            assert len(diagnostic['build']['source_sha256'])==64
            report['build']=diagnostic['build']
            assert request('/api/unit-image?template=receiver',token=token).startswith(b'\x89PNG')
            key=json.loads(request('/api/preview',{'template':'receiver','part':'full'},token))['job_id']
            status=json.loads(request('/api/job?id='+key,token=token));assert status['cancellable']
            cancellation=json.loads(request('/api/cancel-job',{'id':key},token))
            assert cancellation.get('accepted') or cancellation.get('status')=='done'
            key=json.loads(request('/api/configure',{'game_root':str(game),'mod_roots':[]},token))['job_id']
            assert not json.loads(request('/api/cancel-job',{'id':key},token))['accepted']
            for _ in range(100):
                status=json.loads(request('/api/job?id='+key,token=token))
                if status['status']=='done':break
                assert status['status']!='error',status
                time.sleep(.05)
            else:raise AssertionError('Protected job did not finish')
            report.update(packaged_job_controls=True,packaged_unit_images=True)
        catalog=job('/api/gun-catalog',{'template':'receiver'})
        assert catalog['weapons'][0]['available']
        preview=job('/api/gun-preview',{'hull':'receiver','donor':'towed','gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}})
        assert preview['preview_valid'] and not preview['runtime_verified']
        assert 'azimuth' in [m['bone'] for m in preview['hull']['meshes']]
        assert 'azimuth' not in [m['bone'] for m in preview['turret']['meshes']]
        saved=[{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot',
                'pose':{'position':[1,2,3],'rotation':[0,0,10]}},
               {'schema':1,'recipient_place':'coax_slot','donor_place':'coax_slot',
                'pose':{'position':[0,0,0],'rotation':[0,0,0]}}]
        for count in (1,2):
            drafted=job('/api/gun-preview',{'hull':'receiver','donor':'donor','gun_swaps':saved[:count]})
            assert drafted['preview_valid'] and drafted['hull']['meshes'] and not drafted['turret']['meshes']
            assert all(s.get('texture_token') for m in drafted['hull']['meshes'] for s in m['submeshes'])
        pending=job('/api/gun-preview',{'hull':'receiver','donor':'donor','gun_swaps':saved[:1],
            'gun_swap':{'schema':1,'recipient_place':'coax_slot','donor_place':'coax_slot'}})
        assert pending['preview_valid'] and pending['turret']['meshes']
        report['packaged_saved_guns']=True
        design={'assembly_draft':{'schema':1,'guns':[{**r,'donor_template':'donor','donor_eid':'0x9010'} for r in saved]}}
        design['assembly_draft']['guns'][0]['pose']['pivot']=[4,0,0]
        design['hull']={'position':[2,0,0],'rotation':[0,0,15],'pivot':[8,0,0]}
        projected=job('/api/design-preview',{'hull':'receiver','transform':design})
        assert projected['preview_valid'] and projected['hull']['meshes'] and not projected['turret']['meshes']
        catalogued=job('/api/design-catalog',{'hull':'receiver','transform':design})
        assert len(catalogued['weapons'])==2
        candidate=job('/api/design-preview',{'hull':'receiver','transform':design,'pending':{'kind':'turret','donor_template':'donor'}})
        assert candidate['turret']['meshes']
        checked=job('/api/check',{'hull':'receiver','donor':'donor','transform':design})
        assert not checked['ok'] and any(s in checked['message'] for s in ('Conquest roster entry','roster_conquest.set')),checked
        report['packaged_vehicle_design']=True;report['packaged_pivots']=True
        check=job('/api/check',{'hull':'receiver','donor':'towed','transform':{'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'}}})
        assert not check['ok'] and any(text in check['message'] for text in ('Conquest roster entry','roster_conquest.set')),check
        if args.crew_visibility:
            resolved=job('/api/preview',{'template':'receiver','part':'full'})
            assert next(s for s in resolved['seats'] if s['place']=='gunner')['bone']=='coax'
            tr={'gun_swap':{'schema':1,'recipient_place':'main_slot','donor_place':'main_slot'},'crew_hidden':['gunner']}
            internal=job('/api/check',{'hull':'receiver','donor':'towed','transform':tr})
            assert not internal['ok'] and any(text in internal['message'] for text in ('Conquest roster entry','roster_conquest.set')),internal
            tr['crew_hidden']=['nonexistent']
            invalid=job('/api/check',{'hull':'receiver','donor':'towed','transform':tr})
            assert not invalid['ok'] and 'El puesto a ocultar no existe' in invalid['message'],invalid
            report['packaged_visibility_compiler']=True
            report['packaged_visual_macros']=True
        if args.support_vehicle:
            packages=job('/api/support-catalog',{})
            assert len(packages['profiles'])==1 and len(packages['cargo'])==1,packages
            support={'schema':1,'profile':packages['profiles'][0]['id'],'remove_mount':'azimuth',
                     'cargo':[{'asset':packages['cargo'][0]['id'],'position':[0,0,10],'rotation':[0,0,0]}]}
            pv=job('/api/support-preview',{'template':'receiver','support_vehicle':support})
            assert pv['preview_valid'] and pv['cargo']['meshes'] and not pv['runtime_verified']
            assert not any(m['bone']=='azimuth' for m in pv['hull']['meshes'])
            compiled=job('/api/check',{'hull':'receiver','donor':'receiver','transform':{'support_vehicle':support}})
            assert not compiled['ok'] and any(text in compiled['message'] for text in ('Conquest roster entry','roster_conquest.set')),compiled
            support['cargo'][0]['position']=[0,0,float('inf')]
            invalid=job('/api/check',{'hull':'receiver','donor':'receiver','transform':{'support_vehicle':support}})
            assert not invalid['ok'] and 'conversión de apoyo no es válida' in invalid['message'],invalid
            report['packaged_support_catalog']=True
            report['packaged_support_preview']=True
            report['packaged_support_compiler']=True
        if args.extra_armor:
            armor=job('/api/armor-catalog',{})
            assert len(armor['items'])==1,armor
            item=armor['items'][0]
            assert item['materials']==['steel'] and item['thickness']==[12],item
            shown=job('/api/armor-preview',{'asset':item['id']})
            assert shown['meshes'] and shown['preview_valid'] and not shown['runtime_verified']
            value={'asset':item['id'],'parent':'body','position':[10,5,3],'rotation':[0,0,90]}
            compiled=job('/api/check',{'hull':'receiver','donor':'receiver','transform':{'commander_only':True,'extra_armor':[value]}})
            assert not compiled['ok'] and any(s in compiled['message'] for s in ('Conquest roster entry','roster_conquest.set')),compiled
            value['parent']='missing'
            invalid=job('/api/check',{'hull':'receiver','donor':'receiver','transform':{'commander_only':True,'extra_armor':[value]}})
            assert not invalid['ok'] and 'punto de fijación del blindaje ya no existe' in invalid['message'],invalid
            report.update(packaged_armor_catalog=True,packaged_armor_preview=True,packaged_armor_compiler=True)
        if args.smoke_launchers:
            for count in (1,2,3):
                shown=job('/api/smoke-preview',{'tubes':count})
                assert shown['preview_valid'] and not shown['runtime_verified']
                assert len(shown['muzzles'])==count and shown['texture']['width']==64
            value={'tubes':3,'parent':'body','position':[10,5,3],'rotation':[0,0,90]}
            compiled=job('/api/check',{'hull':'receiver','donor':'receiver','transform':{'commander_only':True,'smoke_launchers':[value]}})
            assert not compiled['ok'] and any(s in compiled['message'] for s in ('Conquest roster entry','roster_conquest.set')),compiled
            try:
                request('/api/smoke-preview',{'tubes':1})
                raise AssertionError('Smoke API accepted missing token')
            except urllib.error.HTTPError as error:assert error.code==403
            value['parent']='missing'
            invalid=job('/api/check',{'hull':'receiver','donor':'receiver','transform':{'commander_only':True,'smoke_launchers':[value]}})
            assert not invalid['ok'] and 'punto de fijación del lanzador ya no existe' in invalid['message'],invalid
            report.update(packaged_smoke_models=True,packaged_smoke_preview=True,packaged_smoke_compiler=True)
        html=request('/').decode('utf8')
        if args.corrections:
            for name in ('operation-controls','cancel-operation','inspector-cancel','inspector-progress','inspector-progress-bar','inspector-progress-elapsed','pose-pivot','pivot-reset','pivot-0','cancel-check','cmd-availability'):
                assert 'id="'+name+'"' in html
            report['packaged_operation_panel']=True
        for name in ('operation-select','gun-fields','gun-recipient','gun-donor','gun-blocked'):assert 'id="'+name+'"' in html
        if args.crew_visibility:assert 'id="crew-visibility-field"' in html
        if args.support_vehicle:assert 'id="support-fields"' in html and 'id="cargo-list"' in html
        if args.extra_armor:assert 'id="armor-fields"' in html and 'id="armor-list"' in html
        if args.smoke_launchers:assert 'id="smoke-fields"' in html and 'id="smoke-list"' in html
        digest=hashlib.sha256(request('/assets/app.js')).hexdigest()
        assert digest==hashlib.sha256((ROOT/'workshop/web/assets/app.js').read_bytes()).hexdigest()
        report.update(ok=True,packaged_catalog=True,packaged_preview=True,packaged_compiler=True,token_protected=True,ui_sha256=digest)
    finally:
        if token:
            try:request('/api/shutdown',{},token)
            except OSError:pass
        try:process.wait(timeout=8)
        except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=5)
        report['own_process_stopped']=process.poll() is not None
        (out/'report.json').write_text(json.dumps(report,indent=2),encoding='utf8')
    print(json.dumps(report,indent=2))


if __name__=='__main__':main()
