"""Optional pose centres and native skeleton rebasing; no synthetic events."""
import math
from .geometry import euler_matrix,translation,multiply,parse_model
from .syntax import block,quote,bone_name


def pivot_of(pose):
    value=(pose or {}).get('pivot',[0,0,0])
    if not isinstance(value,list) or len(value)!=3 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>100000 for v in value):
        raise ValueError('El pivote debe tener tres coordenadas finitas válidas.')
    return value


def pose_matrix(pose):
    pose=pose or {};pivot=pivot_of(pose)
    # Preserve the existing position/rotation validation, including old recipes.
    euler_matrix(pose.get('rotation',[0,0,0]),pose.get('position',[0,0,0]))
    return multiply(multiply(translation([v+p for v,p in zip(pose.get('position',[0,0,0]),pivot)]),
        euler_matrix(pose.get('rotation',[0,0,0]))),translation([-v for v in pivot]))


def rebase_joint(document,name,offset):
    """Shift a real joint and compensate its contents, preserving all rest geometry.

    A static child carries the former local origin. Existing animated child
    bones retain their local frames; the existing animation exporter corrects
    the changed joint rest matrix. Volumes bound directly to the joint follow
    the static geometry, not its newly moved origin.
    """
    if not any(abs(v)>1e-10 for v in offset):return None
    from .compiler import _set_matrix
    model=parse_model(document.render());bone=model['bones'][name]
    node=next(n for n in document.find('skeleton').walk('bone') if bone_name(n)==name)
    serial=0
    while 'cw_pivot_'+str(serial) in model['bones']:serial+=1
    child_name='cw_pivot_'+str(serial);child=block('{bone '+quote(child_name)+'}')
    _set_matrix(child,translation([-v for v in offset]))
    for item in list(node.children):
        if item.head.casefold() in ('bone','volumeview','lodview'):
            node.remove(item);child.append(item)
    node.append(child);_set_matrix(node,multiply(bone['local'],translation(offset)))
    for volume in document.find_all('volume'):
        ref=volume.find('bone')
        if ref is not None and ref.args==[name]:ref.atoms[1].set(child_name)
    return child_name


def level_pose(pose):
    """The part of a gun pose its weapon joint follows: position, pivot and the
    turn about the vertical axis. Tilt and roll stay with the gun's model."""
    pose=pose or {};rotation=pose.get('rotation',[0,0,0])
    return {**pose,'rotation':[0,0,rotation[2] if len(rotation)==3 else 0]}


def level_joint(document,name,correction):
    """Keep a weapon joint on its native axes while its model is tilted or rolled.

    `correction` is the static child's local frame (joint world inverse times the
    tilted world). The joint keeps its origin; a static child carries the former
    contents, so every rest pose and child frame is preserved, like rebase_joint.
    """
    from .compiler import _set_matrix
    from .geometry import inverse
    if all(abs(correction[r][c]-(1 if r==c else 0))<1e-9 for r in range(4) for c in range(4)):return None
    model=parse_model(document.render());bone=model['bones'][name]
    node=next(n for n in document.find('skeleton').walk('bone') if bone_name(n)==name)
    serial=0
    while 'cw_fit_'+str(serial) in model['bones']:serial+=1
    child_name='cw_fit_'+str(serial);child=block('{bone '+quote(child_name)+'}')
    _set_matrix(child,correction)
    for item in list(node.children):
        if item.head.casefold() in ('bone','volumeview','lodview'):
            node.remove(item);child.append(item)
    node.append(child);_set_matrix(node,multiply(bone['local'],inverse(correction)))
    for volume in document.find_all('volume'):
        ref=volume.find('bone')
        if ref is not None and ref.args==[name]:ref.atoms[1].set(child_name)
    return child_name
