"""Independent attachment of crew actors; weapon geometry and operators stay put."""
from .syntax import block,quote
from .geometry import parse_model,free_mount_parent,inverse,multiply


def apply_crew_fixed(resources,root,model,choices):
    if choices is None:return []
    from .compiler import CompileError,_place_roles,_set_matrix
    from .crew_visibility import seat_declarations
    if not isinstance(choices,dict) or any(not isinstance(k,str) or type(v) is not bool for k,v in choices.items()):
        raise CompileError('La fijación de tripulantes debe indicar sí o no para cada puesto.')
    declarations,_,_=seat_declarations(resources,root)
    seats={}
    for place in declarations:
        if not place.args:continue
        values=seats.setdefault(place.args[0],{})
        for child in place.children:
            if child.head.casefold() in ('group','linkbone'):values[child.head.casefold()]=child.args[0] if child.args else ''
    reports=[]
    for role,fixed in choices.items():
        values=seats.get(role,{})
        if role not in seats or values.get('group','crew').casefold() not in ('crew','passenger'):raise CompileError('El puesto a fijar no existe en este ensamblaje.\n'+str(role))
        current=parse_model(model['document'].render());seat=values.get('linkbone')
        if not seat or seat not in current['bones']:
            raise CompileError('Este puesto es interno y no tiene una posición visible para fijar.\n'+role)
        static=free_mount_parent(current)
        if not static:raise CompileError('No se pudo determinar dónde fijar al tripulante.\n'+role)
        free_name='cw_crew_follow_'+role;fixed_name='cw_crew_fixed_'+role
        source=current['bones'][seat];world=source['world']
        if free_name in current['bones']:
            following=current['bones'][free_name]['parent']
        else:
            following=seat
            # Legacy carried seats may already be on the chassis. Recover the
            # weapon's turning frame from its explicit native place references.
            chain=[];cursor=following
            while cursor in current['bones'] and current['bones'][cursor]['parent']:
                entry=current['bones'][cursor]
                if 'revolute' in entry['node'].args[:-1]:chain.append(cursor)
                cursor=entry['parent']
            if not chain:
                for weaponry in root.find_all('Weaponry'):
                    for place in weaponry.find_all('place'):
                        if role not in _place_roles(place,implicit_gunner=True):continue
                        names=[n.args[0] for h in ('LinkBone','bone','foresight','shooter') for n in place.find_all(h) if n.args]
                        names+=place.args[:1]
                        for name in names:
                            if name not in current['bones']:continue
                            cursor=name;driven=[]
                            while cursor in current['bones'] and current['bones'][cursor]['parent']:
                                entry=current['bones'][cursor]
                                if 'revolute' in entry['node'].args[:-1]:driven.append(cursor)
                                cursor=entry['parent']
                            if driven:following=driven[-1];break
                        else:continue
                        break
        # Dedicated empty anchors preserve the seat's full reflected frame and
        # leave the original bone/children (possibly gun geometry) untouched.
        for name,parent in ((free_name,following),(fixed_name,static)):
            if name in current['bones']:
                old=current['bones'][name]
                host=current['bones'][old['parent']]['node'] if old['parent'] else current['skeleton']
                host.remove(old['node'])
            anchor=block('{bone '+quote(name)+'}')
            _set_matrix(anchor,multiply(inverse(current['bones'][parent]['world']),world))
            current['bones'][parent]['node'].append(anchor)
        model['document']=current['document'];model.update(parse_model(current['document'].render()))
        chosen=fixed_name if fixed else free_name
        for placer in root.find_all('placer'):
            for place in placer.find_all('place'):
                if place.args==[role]:
                    for link in list(place.find_all('LinkBone')):place.remove(link)
                    place.append(block('{LinkBone '+quote(chosen)+'}'))
        root.append(block('{placer {place '+quote(role)+' {LinkBone '+quote(chosen)+'}}}'))
        reports.append({'place':role,'fixed':fixed,'bone':chosen,'follow_parent':following,'fixed_parent':static})
    return reports
