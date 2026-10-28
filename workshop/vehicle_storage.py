"""Whole vehicles outside the campaign, backed by the normal transaction journal.

The original save and addon remain in a durable entry, outside prunable operations.
Journal state, rather than a second post-publication flag, decides whether an item
is stored or returned. Crew and their inventories travel with their vehicle.
"""
from datetime import datetime
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

from .campaign import (CampaignError, _one, _entity_nodes, _node_eids, _is_slicer, _all_atoms, _EID,
                       _snapshot, _fresh_eid, canonical_eid, load_campaign,
                       build_save_copy, plan_removal, _addon_last)
from .depot import Depot
from .syntax import block
from .transactions import _atomic_json, _read_journal, _no_symlink, TransactionError


class StorageError(ValueError):
    pass


def _seal(plan):
    plan['plan_id']=hashlib.sha256(json.dumps(plan,sort_keys=True,ensure_ascii=False,
                                            separators=(',',':')).encode()).hexdigest()
    return plan


def _remap(node,mapping):
    # Save identifiers can contain uppercase or zero-padded hexadecimal digits.
    for atom in _all_atoms(node):
        if _EID.fullmatch(atom.value) and canonical_eid(atom.value) in mapping:
            replacement=mapping[canonical_eid(atom.value)]
            if replacement!=canonical_eid(atom.value):atom.set(replacement)


def plan_storage(snapshot, vehicle_eid, *, addon_mod='modular_workshop:0'):
    plan=plan_removal(snapshot,vehicle_eid,addon_mod=addon_mod)
    plan.pop('plan_id')
    squad=snapshot.squads[plan['consumed_squad_indices'][0]]
    members=[plan['recipient_eid'],*squad['human_eids']]
    for human in squad['human_eids']:
        if sum(human in s['eids'] for s in snapshot.squads)!=1:
            raise CampaignError('Un tripulante pertenece a más de una escuadra. No se puede guardar el vehículo.')
    plan.update(operation='store_one',stored_eids=members,consumed_eids=members,
                reserve_squads=[],warnings=['Vehicle and its whole crew are stored outside the campaign.'])
    return _seal(plan)


def apply_storage(snapshot, plan):
    if plan!=plan_storage(snapshot,plan['recipient_eid'],addon_mod=plan['addon_mod']):
        raise CampaignError('El plan del Almacén cambió. Volvé a abrir la campaña.')
    scene=snapshot._scene.clone();campaign=_one(scene,'campaign',required=True)
    stored=set(plan['stored_eids']);squads=_one(campaign,'CampaignSquads',required=True)
    row=squads.children[plan['consumed_squad_indices'][0]];squads.remove(row)
    for node in list(campaign.children):
        if node is squads or not (_node_eids(node)&stored):continue
        head=node.head.casefold()
        own=(head in ('entity','human') and len(node.args)==2 and canonical_eid(node.args[1]) in stored)
        inventory=head=='inventory' and node.args and canonical_eid(node.args[0]) in stored
        if own or inventory or head in ('tags','link','linktrailer') or _is_slicer(node):
            # External passengers keep their own entities/squads; only the
            # boarding connection leaves when the stored vehicle leaves.
            if head=='tags' and (_node_eids(node)-stored-{hex(0xffffffff)}):
                raise CampaignError('El vehículo comparte etiquetas con otra unidad. No se pudo separar su estado.')
            campaign.remove(node)
        else:
            raise CampaignError('Hay referencias no reconocidas al vehículo o sus tripulantes. No se modificó la campaña.')
    after=_snapshot(build_save_copy(snapshot,{'campaign.scn':scene.render().encode(snapshot._scene_encoding)}),snapshot.source_path)
    before_nodes=_entity_nodes(_one(snapshot._scene,'campaign',required=True))
    after_nodes=_entity_nodes(_one(after._scene,'campaign',required=True))
    if set(after_nodes)!=set(before_nodes)-stored or _node_eids(_one(after._scene,'campaign'))&stored:
        raise CampaignError('La campaña no pudo separar el vehículo y toda su tripulación.')
    if any(after_nodes[e].render()!=n.render() for e,n in before_nodes.items() if e not in stored):
        raise CampaignError('Se alteró una unidad ajena al Almacén.')
    replacements={'campaign.scn':scene.render().encode(snapshot._scene_encoding)}
    status=snapshot._status.clone();_addon_last(status,plan['addon_mod'],required=False)
    if status.render()!=snapshot._status.render():replacements['status']=status.render().encode(snapshot._status_encoding)
    return replacements


def _stored_records(original, vehicle_eid):
    plan=plan_storage(original,vehicle_eid)
    stored=set(plan['stored_eids']);campaign=_one(original._scene,'campaign',required=True)
    rows=[]
    for node in campaign.children:
        if node.head=='CampaignSquads':continue
        refs=_node_eids(node)-{hex(0xffffffff)}
        if not refs&stored:continue
        head=node.head.casefold()
        if head in ('entity','human') and canonical_eid(node.args[1]) in stored:
            if refs-stored:raise CampaignError('El estado guardado mantiene referencias externas. No se puede devolver todavía.')
            rows.append(node.clone())
        elif head=='inventory' and canonical_eid(node.args[0]) in stored:
            if refs-stored:raise CampaignError('El inventario guardado mantiene referencias externas. No se puede devolver todavía.')
            rows.append(node.clone())
        elif refs<=stored and (head in ('tags','link','linktrailer') or _is_slicer(node)):
            rows.append(node.clone())
    squad=original.squads[plan['consumed_squad_indices'][0]]
    return plan,rows,_one(campaign,'CampaignSquads').children[squad['index']].clone()


def plan_restoration(snapshot, original, vehicle_eid, *, addon_mod='modular_workshop:0'):
    if snapshot.game_version!=original.game_version:
        raise CampaignError('La versión del juego no coincide con la del vehículo guardado.')
    stored,records,squad=_stored_records(original,vehicle_eid)
    campaign=_one(snapshot._scene,'campaign',required=True).clone()
    used=_node_eids(campaign);mapping={}
    for old in stored['stored_eids']:
        new=old if old not in used else _fresh_eid(campaign)
        mapping[old]=new;used.add(new);campaign.append(block('{reserved '+new+'}'))
    return _seal({'schema_version':1,'operation':'restore_stored','source_hash':snapshot.source_hash,
                  'archive_path':str(original.source_path),'archive_hash':original.source_hash,
                  'destination_path':str(snapshot.source_path),
                  'original_eid':canonical_eid(vehicle_eid),'new_eid':mapping[stored['recipient_eid']],
                  'new_template':stored['removed_template'],'eid_mapping':mapping,'addon_mod':addon_mod,
                  'consumed_eids':[],'crew_slots':len(squad.args)-2,
                  'restored_eids':list(mapping.values())})


def apply_restoration(snapshot, plan):
    original=load_campaign(plan['archive_path'])
    if original.source_hash!=plan['archive_hash']:
        raise CampaignError('La copia del Almacén cambió. Se conservó sin devolver el vehículo.')
    expected=plan_restoration(snapshot,original,plan['original_eid'],addon_mod=plan['addon_mod'])
    if expected!=plan:raise CampaignError('El plan del Almacén cambió. Volvé a abrir la campaña.')
    _,records,squad=_stored_records(original,plan['original_eid'])
    scene=snapshot._scene.clone();campaign=_one(scene,'campaign',required=True)
    mids={int(n.find('MID').args[0]) for n in _entity_nodes(campaign).values() if n.find('MID') and n.find('MID').args}
    for node in records:
        _remap(node,plan['eid_mapping'])
        if node.head.casefold() in ('entity','human'):
            mid=node.find('MID')
            if mid is not None and mid.args:
                value=int(mid.args[0])
                if value in mids:
                    value=max(mids,default=0)+1;mid.atoms[1].set(str(value))
                mids.add(value)
        campaign.append(node)
    _remap(squad,plan['eid_mapping'])
    # Return to reserve, independently of deployments/battles played while stored.
    squad.atoms[1].set('');_one(campaign,'CampaignSquads',required=True).append(squad)
    # Conquest supports captured foreign squads. Keep the original squad scope
    # and crew breeds; changing (rus) into (ger) would refer to another roster.
    replacements={'campaign.scn':scene.render().encode(snapshot._scene_encoding)}
    status=snapshot._status.clone()
    _addon_last(status,plan['addon_mod'],required=bool(re.fullmatch(r'mw_[A-Za-z0-9_-]+',plan['new_template'])))
    if status.render()!=snapshot._status.render():
        replacements['status']=status.render().encode(snapshot._status_encoding)
    after=_snapshot(build_save_copy(snapshot,replacements),snapshot.source_path)
    old=_entity_nodes(_one(snapshot._scene,'campaign'));new=_entity_nodes(_one(after._scene,'campaign'))
    if set(new)!=set(old)|set(plan['restored_eids']):
        raise CampaignError('No se pudo devolver exactamente el vehículo y su tripulación.')
    if any(new[e].render()!=node.render() for e,node in old.items()):
        raise CampaignError('Se alteró una unidad ajena al Almacén.')
    if any(s['missing_eids'] for s in after.squads):
        raise CampaignError('La campaña tiene referencias de escuadra sin resolver.')
    return replacements


class VehicleStorage:
    def __init__(self,workspace):
        self.root=Path(workspace)/'vehicle_storage';self.problems=[]

    def allocate(self):
        _no_symlink(self.root)
        identifier=secrets.token_hex(16);folder=self.root/identifier
        folder.mkdir(parents=True,exist_ok=False)
        return identifier,folder

    @contextmanager
    def _claim_lock(self):
        # Shared across application instances. Claiming an entry must precede
        # publication to *any* destination, rather than relying on save hashes.
        path=self.root/'transfer.lock';_no_symlink(path)
        with path.open('a+b') as handle:
            handle.seek(0,2)
            if not handle.tell():handle.write(b'\0');handle.flush()
            handle.seek(0)
            try:
                if os.name=='nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
                else:
                    import fcntl
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError as error:
                raise StorageError('Otro traslado del Almacén está en curso. Volvé a intentarlo.') from error
            try:yield
            finally:
                handle.seek(0)
                if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
                else:fcntl.flock(handle,fcntl.LOCK_UN)

    def _folder(self,identifier):
        if not isinstance(identifier,str) or not re.fullmatch(r'[a-f0-9]{32}',identifier):
            raise StorageError('Ese vehículo no está en el Almacén de esta campaña.')
        folder=self.root/identifier
        _no_symlink(folder)
        return folder

    def _load(self,identifier,save):
        folder=self._folder(identifier)
        try:data=json.loads((folder/'entry.json').read_text('utf-8'))
        except (OSError,ValueError) as error:raise StorageError('No se pudo leer el vehículo del Almacén. Se conservan sus copias.') from error
        if not isinstance(data,dict):
            raise StorageError('No se pudo leer el vehículo del Almacén. Se conservan sus copias.')
        if data.get('id')!=identifier:
            raise StorageError('Ese vehículo no está en el Almacén de esta campaña.')
        journal=self._journal(folder,data['store_journal'])
        if data.get('campaign')!=Depot.key(journal['source_path']):
            raise StorageError('El registro del Almacén no es válido.')
        return folder,data

    def _journal(self,folder,value):
        _no_symlink(Path(value))
        path=Path(value).resolve()
        if not path.is_relative_to(folder.resolve()):raise StorageError('El registro del Almacén no es válido.')
        return _read_journal(path)[1]

    def create(self,identifier,save,unit,crew,transaction,*,crew_slots=None,campaign_name=None,army=None):
        folder=self._folder(identifier)
        data={'schema':1,'id':identifier,'campaign':Depot.key(save),'template':unit['template'],
              'eid':unit['eid'],'label':unit.get('label') or unit['template'],'crew':crew,
              'created':datetime.now().isoformat(timespec='seconds'),'crew_slots':crew_slots,
              'campaign_name':campaign_name or Path(save).stem,'army':army or '',
              'store_journal':transaction['journal_path'],'restores':[]}
        self._journal(folder,data['store_journal']);_atomic_json(folder/'entry.json',data)

    def state(self,folder,data):
        store=self._journal(folder,data['store_journal'])
        if store['state'] in ('prepared','rolled_back'):return 'inactive',data['store_journal']
        if store['state']!='committed':return 'pending',data['store_journal']
        for path in reversed(data['restores']):
            journal=self._journal(folder,path)
            if journal['state']=='committed':return 'restored',path
            if journal['state'] not in ('rolled_back',):return 'pending',path
        return 'stored',data['store_journal']

    def items(self,save):
        rows=[];self.problems=[]
        for path in sorted(self.root.glob('*/entry.json')):
            try:
                data=json.loads(path.read_text('utf-8'))
                if not isinstance(data,dict):raise StorageError('Invalid storage metadata')
                folder,data=self._load(path.parent.name,save);state,journal=self.state(folder,data)
                if state not in ('stored','pending'):continue
                source=self._journal(folder,data['store_journal'])['source_path']
                pending_source=self._journal(folder,journal)['source_path']
                rows.append({k:data[k] for k in ('id','template','label','crew','created')}|
                            {'kind':'vehicle','stored':True,'pending':state=='pending',
                             'origin_campaign':data.get('campaign_name') or Path(source).stem,
                             'origin_army':data.get('army') or '',
                             'recoverable':Path(save).resolve()==Path(pending_source).resolve(),
                             'pending_campaign':Path(pending_source).stem})
            except (OSError,ValueError,KeyError,TypeError,AttributeError,TransactionError):
                self.problems.append('No se pudo leer un vehículo del Almacén. Se conservan sus copias y los demás siguen disponibles.')
        return rows

    def get(self,identifier,save):
        folder,data=self._load(identifier,save);state,_=self.state(folder,data)
        if state=='pending':raise StorageError('Hay un traslado interrumpido. Usá Recuperar traslado antes de continuar.')
        if state!='stored':raise StorageError('Ese vehículo ya no está disponible en el Almacén.')
        journal=self._journal(folder,data['store_journal'])
        backup=Path(journal['operation_dir'])/'backup'/'original.sav'
        original=load_campaign(backup)
        if original.source_hash!=journal['source_hash']:
            raise StorageError('La copia del Almacén cambió. Se conservó sin devolver el vehículo.')
        return folder,data,original,journal

    def restoring(self,identifier,save,transaction):
        with self._claim_lock():
            folder,data=self._load(identifier,save)
            state,_=self.state(folder,data)
            if state!='stored':
                raise StorageError('Ese vehículo ya no está disponible en el Almacén.')
            journal=self._journal(folder,transaction['journal_path'])
            if Path(journal['source_path']).resolve()!=Path(save).resolve():
                raise StorageError('La campaña cambió. Volvé a abrirla antes de trasladar el vehículo.')
            data['restores'].append(transaction['journal_path']);_atomic_json(folder/'entry.json',data)

    def pending(self,identifier,save):
        folder,data=self._load(identifier,save);state,journal=self.state(folder,data)
        if state!='pending':raise StorageError('No hay un traslado pendiente que recuperar.')
        if Path(self._journal(folder,journal)['source_path']).resolve()!=Path(save).resolve():
            raise StorageError('Abrí la partida del traslado interrumpido para recuperarlo.')
        return journal
