"""Pure 4.10.2 catalog planning. Public baselines are embedded literals.

No files, network, Wingman imports, clocks, random ids or threads are used.
The caller owns persistence and must apply binds before rebuilding its live index.
"""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from . import compat as core
from .data import LAYOUTS, PROFILE
from .command_catalog import command_text, FOCUS_ACTIONS
from . import emotes

# Only base names evidenced by 4.10.2 defaults / Tablet Deck and allowed by
# review r1. Preferred profile combos must satisfy this same pool policy.
# Stable order: F1–F12, letters, digits, punctuation. No extended/numpad sends.
BASE_KEYS = (tuple(f'f{n}' for n in range(1, 13)) + tuple('abcdefghijklmnopqrstuvwxyz')
             + tuple('0123456789')
             + ('comma', 'period', 'minus', 'equals', 'slash', 'semicolon',
                'backslash', 'lbracket', 'rbracket'))
BIND_MODIFIERS = ('rctrl', 'lctrl', 'ralt', 'lalt')
CANDIDATE_POOL = tuple(f'{mod}+{base}' for mod in BIND_MODIFIERS for base in BASE_KEYS)
# Wingman defaults: cancel speech and voice activation. Player record/mute/cancel
# hotkeys are also read through the public configuration facade at runtime.
RESERVED = {'lshift+y', 'lshift+x'}
DROP = {
 'v_ifcs_speed_limiter_reset_scm': 'No faithful speed-limiter reset action exists in 4.10.2.',
 'v_speed_range_up': 'Absent in 4.10.2; acceleration range is not speed range.',
 'v_speed_range_down': 'Absent in 4.10.2; acceleration range is not speed range.',
 'v_target_match_vel': 'No match-velocity action exists in 4.10.2.',
}
MACROS = ('Launch Sequence', 'Landing Sequence', 'Shut Down and Lock Up',
          'Prepare Quantum', 'View 1', 'View 2', 'View 3',
          'Save View 1', 'Save View 2', 'Save View 3')
EFFECTS = {
 'v_toggle_landing_system': [('gear', None)], 'v_deploy_landing_system': [('gear', True)],
 'v_retract_landing_system': [('gear', False)], 'v_vtol_toggle': [('vtol', None)],
 'v_vtol_on': [('vtol', True)], 'v_vtol_off': [('vtol', False)],
 'v_power_toggle': [('power', None)], 'v_power_set_on': [('power', True)],
 'v_power_set_off': [('power', False), ('flight-ready', False)],
 'v_power_toggle_thrusters': [('thruster-power', None)],
 'v_power_set_thrusters_on': [('thruster-power', True)],
 'v_power_set_thrusters_off': [('thruster-power', False)],
 'v_power_toggle_weapons': [('weapons-power', None)],
 'v_power_set_weapons_on': [('weapons-power', True)],
 'v_power_set_weapons_off': [('weapons-power', False)],
 'v_power_toggle_shields': [('shield-power', None)],
 'v_power_set_shields_on': [('shield-power', True)],
 'v_power_set_shields_off': [('shield-power', False)],
 'v_flightready': [('flight-ready', True), ('power', True), ('thruster-power', True),
                   ('weapons-power', True), ('shield-power', True)],
 'v_lights': [('lights', None)], 'v_lights_on': [('lights', True)], 'v_lights_off': [('lights', False)],
 'v_toggle_all_doors': [('doors', None)], 'v_open_all_doors': [('doors', True)],
 'v_close_all_doors': [('doors', False)], 'v_lock_all_doors': [('door-lock', True)],
 'v_unlock_all_doors': [('door-lock', False)],
 'v_ifcs_speed_limiter_toggle': [('speed-limiter', None)],
 'v_ifcs_speed_limiter_on': [('speed-limiter', True)],
 'v_ifcs_speed_limiter_off': [('speed-limiter', False)],
 'v_ifcs_toggle_esp': [('esp', None)], 'v_ifcs_toggle_gforce_safety': [('g-safety', None)],
}


def walk(value):
    if isinstance(value, dict):
        if isinstance(value.get('keys'), str):
            yield value
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class Index:
    def __init__(self, raw, xml=None):
        self.actions = core.merge_overrides(raw, xml)
        self.command_inputs = {}
        for row in self.actions:
            for device in ('keyboard', 'mouse'):
                for bind in row.get(device, []):
                    self.command_inputs.setdefault(core.normalize_key(bind['input']), []).append((row, bind))


def sync_step(name, skill):
    return {'skill_action': {'skill_name': skill, 'function_name': 'deck_sync',
                              'parameters': {'command': name}}}


def _sync_matches(action, name, skill):
    """Accept Wingman's nullable model_dump shape without touching key steps."""
    if not isinstance(action, dict):
        return False
    return core.command_fingerprint({'actions':[action]}) == core.command_fingerprint(
        {'actions':[sync_step(name,skill)]})


def key_steps(key, hold=.1):
    """Wingman's executor notation, including separate right modifiers.

    num N / decimal / add / subtract are keyboard's Windows numpad names;
    Right Ctrl uses scan 29 + extended, matching Wingman's recorder; the
    name-only keyboard.send path otherwise emits Left Ctrl. F-keys stay fN.
    """
    names = {'ralt': 'alt gr', 'lalt': 'alt', 'rctrl': 'right ctrl', 'lctrl': 'ctrl',
             'rshift': 'right shift', 'lshift': 'shift', 'return': 'enter',
             'np_period': 'decimal', 'np_add': 'add', 'np_subtract': 'subtract',
             'np_multiply': 'multiply', 'np_divide': 'divide', 'np_enter': 'num enter',
             'pgup': 'page up', 'pgdn': 'page down', 'comma': ',', 'period': '.',
             'minus': '-', 'equals': '=', 'semicolon': ';', 'apostrophe': "'",
             'backslash': '\\', 'slash': '/', 'lbracket': '[', 'rbracket': ']'}
    def wing(token):
        return 'num '+token[-1] if re.fullmatch(r'np_\d', token) else names.get(token, token)
    tokens = core.normalize_key(key).split('+')
    mods = [t for t in tokens if t in core.MODIFIERS]
    base = [t for t in tokens if t not in core.MODIFIERS]
    if len(base) != 1:
        raise ValueError('Expected one base key: '+key)
    mouse = {'mouse1':'left','mouse2':'right','mouse3':'middle','mouse4':'x','mouse5':'x2'}
    def modifier_step(mod, direction):
        keyboard = {'hotkey': wing(mod), direction: True}
        if mod == 'rctrl':
            keyboard.update(hotkey_codes=[29], hotkey_extended=True)
        return {'keyboard': keyboard}
    split = bool({'ralt', 'rctrl'} & set(mods)) or base[0] in mouse
    if split:
        result = [modifier_step(m, 'press') for m in mods]
        if mods:
            result.append({'wait': .35})
        result.append({'mouse': {'button':mouse[base[0]], 'hold':hold}} if base[0] in mouse
                      else {'keyboard': {'hotkey':wing(base[0]), 'hold':hold}})
        if mods:
            result.append({'wait':.1})
        result.extend(modifier_step(m, 'release') for m in reversed(mods))
        return result
    return [{'keyboard': {'hotkey': '+'.join(wing(t) for t in tokens), 'hold':hold}}]


def _target(button, rows):
    """Resolve intended action, not whatever the erroneous upstream key fires."""
    action = button.get('scAction')
    correction = ''
    if button.get('id') in {'cb-missile', 'missile-launch'}:
        # The source assigns the cinematic-camera toggle to Launch Missile.
        # Preserve its visuals and preferred key, but deliver the promised shot.
        action = 'v_weapon_toggle_launch_missile'
        correction = ('Launch Missile remapped from the cinematic-camera toggle to '
                      'Launch Missiles (Tap); works only in missile operator mode.')
    if action in DROP:
        return None, DROP[action]
    if action == 'v_ifcs_toggle_speed_limiter':
        action = 'v_ifcs_speed_limiter_toggle'
    key, label = button['keys'], button['label'].lower()
    direct = {
        'r':'v_flightready', 'u':'v_power_toggle', 'p':'v_power_toggle_weapons',
        'o':'v_power_toggle_shields', 'k':'v_vtol_toggle', 'l':'v_lights',
        'f1':'mobiglas', 'f2':'v_starmap', 'f11':'toggle_contact', 'f12':'toggle_chat',
        '0':'v_dock_toggle_view', 'f8':'v_engineering_assignment_reset',
        'f4':'v_view_cycle_fwd', 'escape':'ui_toggle_pause', 'y':'pl_exit',
        'g':'v_weapon_countermeasure_decoy_launch', 'h':'v_weapon_countermeasure_noise_launch',
        'ralt+y':'v_eject', 'backspace':'v_self_destruct',
    }
    if not action:
        action = direct.get(key)
        if key == 'i': action = 'pc_pit_inventory' if 'inventory' in label else 'v_power_toggle_thrusters'
        if key == 'n': action = 'v_autoland' if button.get('mode') == 'timed' else 'v_toggle_landing_system'
        if key == 'lalt+n': action = 'v_toggle_docking_request' if 'dock' in label else 'v_atc_request'
        if key == 'b': action = 'v_toggle_qdrive_engagement' if button.get('mode') == 'hold' else 'v_master_mode_cycle_long'
        if re.fullmatch(r'(lalt\+)?f[567]', key):
            part = {'5':'weapons','6':'engine','7':'shields'}[key[-1]]
            suffix = ('min' if key.startswith('lalt') else 'max') if button.get('mode') == 'timed' else ('decrease' if key.startswith('lalt') else 'increase')
            action = f'v_engineering_assignment_{part}_{suffix}'
    found = [r for r in rows if r['name'] == action
             and (not correction or r['map'] == 'spaceship_missiles')]
    if not found:
        return None, 'Action not found in supplied index: '+str(action)
    # Prefer Tablet Deck's verified map, then ship map, not player context aliases.
    profile_maps = {ident.rsplit('.',1)[0] for ident in PROFILE if ident.endswith('.'+action)}
    found.sort(key=lambda r: (r['map'] not in profile_maps,
                             not r['map'].startswith('spaceship'), r['map']))
    return found[0], correction


def _effects(command, idx, steps, skill):
    recorded = steps.get(command['name'], {})
    # The old intent fingerprint remains useful after adding only our silent step.
    actions = command.get('actions') or []
    bare = {**command, 'actions': actions[:-1] if actions and _sync_matches(actions[-1],command['name'],skill) else actions}
    if recorded.get('fingerprint') in {core.command_fingerprint(bare),core.command_fingerprint(command)}:
        action_sets = [s['actions'] for s in recorded.get('steps', [])]
    else:
        action_sets = [[r['map']+'.'+r['name'] for r,b in core.matches(e, command, idx)]
                       for e in core.events(command)]
    result = []
    for actions in action_sets:
        # Identical action names in vehicle/ship aliases must never flip twice.
        names = sorted({ident.rsplit('.',1)[-1] for ident in actions})
        for name in names:
            for group, value in EFFECTS.get(name, []):
                effect = {'group':group, 'set':value}
                result.append(effect)
    # Keep sequential macro intent, including set then toggle; don't flatten it.
    return result


def _spoken(label):
    label = re.sub(r'\s*\+$', ' up', label.lower())
    label = re.sub(r'\s*[−-]$', ' down', label)
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9' ]", ' ', label.replace('&', ' and '))).strip()


def _panels(layout):
    """Same fixed canvas, two region pages and grid geometry as make-*.js."""
    macros = [{'id':'astra-'+re.sub(r'[^a-z0-9]+','-',n.lower()), 'label':n,
               'keys':'', 'mode':'tap', '_macro':n} for n in MACROS]
    nav = [{'id':'nav-'+ident, 'label':label, 'keys':'', 'mode':'tap', '_nav':ident,
            'color':'blue'} for ident,label in
           [('mark','Mark Location'),('clear','Clear Target')]]
    reset = {'id':'deck-reset-lights','label':'Reset Lights','keys':'','mode':'tap','_ui':'reset-states'}
    if 'canvas' in layout:
        region = layout['regions'][1]
        body = region['h']-region.get('pager',44)-8
        layout['panels']['astra'] = {'title':'Commands','elements':[
            {'type':'header','text':'Commands','x':2,'y':0},
            {'type':'grid','x':0,'y':24,'w':region['w'],'h':body-24,'cols':2,
             'compact':layout['canvas'][1]<500,'buttons':macros}]}
        # Data panels use the shared desk renderer; action definitions remain in
        # the manifest for the established in-process dispatch/guard contract.
        layout['panels']['nav'] = {'title':'NAV','navdesk':'nav','elements':[], 'buttons':nav+[reset]}
        layout['panels']['waypoints'] = {'title':'WAYPOINTS','navdesk':'waypoints','elements':[]}
        region['panels'].extend(['astra','nav','waypoints'])
    else:
        layout['pages'].extend([{'name':'Commands','buttons':macros},
                                {'name':'NAV','navdesk':'both','buttons':nav+[reset]}])


def plan_pack(index, profile_xml, custom_binds, commands, steps, settings):
    """Return additions, silent-step updates and a complete serializable manifest.

    Optional settings['reserved_keys'] extends the fixed hotkey exclusions when
    the host can supply runtime record/mute/cancel hotkeys. It never narrows them.
    """
    if settings.get('setup', True) is False:
        plan=plan_pack(index,profile_xml,custom_binds,commands,steps,{**settings,'setup':True,'preview':True})
        current={c['name']:c for c in commands or []}
        live=Index(index,profile_xml)
        for layout in [plan['deck']['buttons'], *plan['deck']['layouts']['files'].values()]:
            for button in walk(layout):
                kind=button.get('kind')
                if kind=='command':
                    command=current.get(button.get('command'))
                    target=button.get('actionmap','')+'.'+button.get('scAction','')
                    button['not_set_up']=not command or (bool(button.get('scAction')) and
                        target not in core.resolved(command,live,curated=bool(button.get('emote'))))
                    if button.get('emote'):button['no_key']=not command or not emotes.verified(command,target,live)
                elif kind=='hold':
                    target=button.get('hold',{})
                    button['not_set_up']=not any(r['map']==target.get('map') and r['name']==target.get('action')
                        for r,_ in live.command_inputs.get(core.normalize_key(target.get('input','')),[]))
                button.pop('pair',None)  # only live pack setup certifies paired dispatch
        for field in ('binds_to_replace','binds_to_add','commands_to_add','commands_to_update','manifest_entries'):
            plan[field]=[]
        return plan
    skill = settings.get('skill_name','SC_VoiceCockpit')
    existing = {c['name']:copy.deepcopy(c) for c in commands or []}
    # Custom binds are restore intent. A real profile bind always wins over it.
    base_rows = core.merge_overrides(index, profile_xml)
    profile_root = ET.fromstring(profile_xml) if profile_xml else ET.Element('ActionMaps')
    profile_slots = {(m.get('name'), a.get('name'), *core._slot(b.get('input'))[:2])
                     for m in profile_root.iter('actionmap') for a in m.findall('action')
                     for b in a.findall('rebind') if b.get('input') is not None}
    restore_xml = ET.Element('ActionMaps')
    for b in custom_binds or []:
        if (b['map'],b['action'],*core._slot(b['input'])[:2]) not in profile_slots:
            a=ET.SubElement(ET.SubElement(restore_xml,'actionmap',name=b['map']),'action',name=b['action'])
            ET.SubElement(a,'rebind',input=b['input'])
    idx = Index({'actions':base_rows}, ET.tostring(restore_xml,encoding='unicode'))
    occupied = {core.normalize_key(b.get('input','')) for rows in (index['actions'],idx.actions)
                for r in rows for d in core.DEVICES for b in r.get(d,[]) if b.get('input')}
    occupied.update(core.normalize_key(b['input']) for b in custom_binds or [])
    occupied.update(core.normalize_key(b.get('input')) for b in profile_root.iter('rebind') if b.get('input'))
    occupied.update(core.normalize_key(k) for k in RESERVED | set(settings.get('reserved_keys',[])))
    rows = {(r['map'],r['name']):r for r in idx.actions}
    default_ids = {(r['map'],r['name']) for r in index['actions']}
    binds, added, updates, warnings, decisions = [], [], [], [], []
    layouts = copy.deepcopy(LAYOUTS)
    for layout in layouts.values(): _panels(layout)
    # Process HUD first so short labels from grid variants don't determine names.
    files = ['hud.json']+[f for f in sorted(layouts) if f!='hud.json']
    buttons, cmdmeta, selected = {}, {}, {}
    all_phrases = {p.casefold().strip() for c in existing.values() for p in c.get('instant_activation') or []}
    emote_catalog = {b.get('actionmap','player_emotes')+'.'+b['scAction']:b['label']
                     for l in layouts.values() for b in walk(l) if b.get('scAction','').startswith('emote_')}
    replacements, repairs = emotes.migrate(idx, profile_root, custom_binds or [], existing, occupied,
                                           CANDIDATE_POOL, emote_catalog, skill)
    updates.extend(repairs)
    effect_cache = {n:_effects(c,idx,steps or {},skill) for n,c in existing.items()}
    command_candidates = {}
    for name,c in existing.items():
        evs=core.events(c)
        if len(evs)!=1:continue
        reached={r['map']+'.'+r['name'] for r,_ in core.matches(evs[0],c,idx)}
        # SC's decoy bind supports press/release *and* hold; 1.4.2's
        # conservative auditor omits its tap because holdTriggerDelay exists.
        reached.update(r['map']+'.'+r['name'] for r,b in idx.command_inputs.get(evs[0][0],[])
                       if core.relevant(r,c) and not b.get('activationMode')
                       and b.get('onPress')=='1' and b.get('onRelease')=='1')
        for target in reached:command_candidates.setdefault(target,[]).append((len(reached),name))

    def attach(b, command, effects):
        b.update(kind='command',command=command,guard=True,respond=True,mode='tap')
        meta=cmdmeta.setdefault(command,{'buttons':[],'effects':effects})
        if b['id'] not in meta['buttons']:meta['buttons'].append(b['id'])
        buttons.setdefault(b['id'],copy.deepcopy(b))

    def convert(b, file):
        ident=b['id']
        if '_nav' in b:
            action=b.pop('_nav'); b.update(kind='nav',nav={'mark':'mark_location','hud':'show_navpoint_hud',
                     'clear':'clear_target','dashboard':'open_dashboard'}[action],guard=False,respond=True)
            if action=='dashboard':b['url']='/nav'
            buttons.setdefault(ident,copy.deepcopy(b));return True
        if '_ui' in b:
            b.update(kind='ui',ui=b.pop('_ui'),respond=False)
            buttons.setdefault(ident,copy.deepcopy(b));return True
        if '_macro' in b:
            name=b.pop('_macro')
            if name not in existing:
                if settings.get('preview'):
                    b.update(kind='command',command=name,not_set_up=True,guard=True,respond=False)
                    buttons.setdefault(ident,copy.deepcopy(b));return True
                warnings.append('Macro unavailable: '+name);return False
            attach(b,name,effect_cache[name]);return True
        row, reason = _target(b, idx.actions)
        if row is None or (row['map'],row['name']) not in default_ids:
            decisions.append({'layout':file,'id':ident,'decision':'dropped','reason':reason or 'Not in defaults index.'});return False
        if row['name'].startswith('emote_') and not settings.get('emotes',True):
            decisions.append({'layout':file,'id':ident,'decision':'disabled','action':row['map']+'.'+row['name'],
                              'reason':'Emotes disabled in settings.'})
            return False
        target=row['map']+'.'+row['name']
        oldkey=b['keys']; hold=max(.1,float(b.get('ms',100))/1000) if b.get('mode')=='timed' else .1
        if row['name'] in ('v_master_mode_cycle_long','v_toggle_qdrive_engagement'):hold=2.0
        # Choose an existing *single-event* command with matching action intent;
        # never substitute an explicit-set action or an entire macro for a toggle.
        candidates=command_candidates.get(target,[])
        if row['name']=='v_weapon_toggle_launch_missile':
            # Mouse1 is shared with gun fire and other operator modes. Reusing
            # a mouse command would hide this launch action and skip its required
            # keyboard bind; a prior keyboard launch command remains reusable.
            candidates=[c for c in candidates if not any(
                t.startswith(('mouse','mwheel','maxis'))
                for t in core.events(existing[c[1]])[0][0].split('+'))]
        chosen = sorted(candidates)[0][1] if candidates else None
        if row['name'].startswith('emote_'):
            owned_name = command_text(row['name'], b['label'])[0]
            owned = existing.get(owned_name)
            if owned and (owned.get('actions') or []) and _sync_matches(owned['actions'][-1],owned_name,skill):
                # Keep a cleared/edited emote visible, never allocate over the user's choice.
                chosen = owned_name
        if target in selected: chosen,key = selected[target]
        else:
            key=''
            # Prefer actual player/custom keyboard binding, then keyboard default.
            for binding in row.get('keyboard',[]):
                candidate=core.normalize_key(binding['input'])
                if candidate and not any(t.startswith(('mouse','mwheel','maxis')) for t in candidate.split('+')):
                    key=candidate;break
            if chosen:
                evs=core.events(existing[chosen])
                key=evs[0][0] if evs else ''
            if not key and not chosen:
                preferred=core.normalize_key(PROFILE.get(target, oldkey))
                safe=lambda k: k in CANDIDATE_POOL and k not in occupied
                key=next((k for k in (preferred,)+CANDIDATE_POOL if safe(k)),None)
                if key is None:
                    warnings.append('No collision-free bind for '+target);return False
                binds.append({'map':row['map'],'action':row['name'],'input':'kb1_'+key})
                occupied.add(key)
                # Update only our in-memory effective row for later linking.
                # SC may store mouse1 in the keyboard slot. Replacing that slot
                # must also remove its old lookup, or manifests still claim the
                # displaced mouse action and upstream reports false drift.
                for old_binding in row.get('keyboard',[]):
                    old_input=core.normalize_key(old_binding['input'])
                    idx.command_inputs[old_input]=[(r,b) for r,b in idx.command_inputs.get(old_input,[])
                                                  if not (r is row and b is old_binding)]
                row['keyboard']=[{'input':'kb1_'+key,'activationMode':row.get('activationMode','')}]
                idx.command_inputs.setdefault(key,[]).append((row,row['keyboard'][0]))
                decisions.append({'layout':file,'id':ident,'decision':'bound','action':target,
                    'input':key,'reason':'Tablet Deck profile combo free across every map.' if key==preferred
                    else 'Profile combo '+preferred+' occupied/reserved or outside the single-modifier pool; first free proven single-modifier combo.'})
            selected[target]=(chosen,key)
        for binding in row.get('keyboard',[]):
            if core.normalize_key(binding['input'])!=key:continue
            if (binding.get('activationMode') or '').startswith('delayed_'):
                hold=max(hold,float(binding.get('pressTriggerThreshold',-1))+.1)
            if binding.get('onHold')=='1' and binding.get('onPress')!='1':
                hold=max(hold,float(binding.get('holdTriggerDelay',0))+.1)
        b['keys']=key;b['scAction']=row['name'];b['actionmap']=row['map']
        effects=[{'group':g,'set':v} for g,v in EFFECTS.get(row['name'],[])]
        if effects:
            first=effects[0];b['state']={'group':first['group'],'set':first['set'],
                                       'on':(b.get('state') or {}).get('on','ON')}
        else:b.pop('state',None)
        is_hold=(row['name'] in {'v_eject','v_self_destruct','v_emergency_exit','v_view_look_behind',
                                  'v_decrease_mining_throttle','v_increase_mining_throttle'})
        if is_hold:
            b.update(kind='hold',hold={'map':row['map'],'action':row['name'],'input':
                     ('kb1_' if not key.startswith('mouse') else 'mo1_')+key},guard=True,respond=False,voice=False)
            # Slider stays timed: its own slide confirmation sends tap, which the
            # host translates to bounded down/up. Other holds send down/up.
            if b.get('type')!='slider':b['mode']='hold'
            buttons.setdefault(ident,copy.deepcopy(b))
        else:
            if not chosen:
                name, description = command_text(row['name'], b['label'])
                # A colliding command name is not evidence of matching intent. The
                # candidate search above already found every reusable single-event
                # command; never silently reuse a macro or a second name collision.
                base_name=name
                suffix=1
                while name in existing:
                    name=base_name+' (Deck'+(' '+str(suffix) if suffix>1 else '')+')'
                    suffix+=1
                if name not in existing:
                    phrases=['emote '+_spoken(b['label'])] if row['name'].startswith('emote_') else ([ _spoken(b['label'])]+(b.get('voice') if isinstance(b.get('voice'),list) else []))
                    if row['name']=='ui_toggle_pause':phrases.append('press escape')
                    if row['name']=='v_weapon_toggle_launch_missile':
                        phrases=['fire missile','fire missiles','missile away','launch missile']
                    phrases=[p for p in dict.fromkeys(p.casefold().strip() for p in phrases) if len(p.split())>1 and p not in all_phrases]
                    if row['name']=='pl_exit':phrases=[p for p in phrases if p!='get up']
                    # Do not give toggles state-setting instant phrases.
                    if any(e['set'] is None for e in effects):phrases=[p for p in phrases if not re.search(r'\b(on|off)\b',p)]
                    all_phrases.update(phrases)
                    c={'name':name,'description':description,
                       'instant_activation':phrases,'force_instant_activation':row['name'].startswith('emote_') or row['name'] in FOCUS_ACTIONS,
                       'actions':key_steps(key,hold)+[sync_step(name,skill)]}
                    added.append(c);existing[name]=c;effect_cache[name]=effects
                chosen=name
            selected[target]=(chosen,key)
            if row['name'].startswith('emote_'):
                b['emote'] = target
                b['no_key'] = not emotes.verified(existing[chosen], target, idx)
            attach(b,chosen,effect_cache.get(chosen,effects))
            if row['name'].startswith('emote_'):
                cmdmeta[chosen].update(emote=target, label=b['label'], no_key=b['no_key'])
        decisions.append({'layout':file,'id':ident,'decision':'remapped' if reason or key!=oldkey else 'kept',
                          'action':target,'input':key,'kind':b['kind'], 'original_input':oldkey,
                          'reason':reason or 'Verified action in supplied defaults; resolved against player overrides, then defaults.'})
        return True

    def transform(value,file):
        if isinstance(value,dict):
            if isinstance(value.get('keys'),str):
                if not convert(value,file):return None
            for k,v in list(value.items()):
                if isinstance(v,(list,dict)):value[k]=transform(v,file)
            return value
        if isinstance(value,list):return [v for x in value if (v:=transform(x,file)) is not None]
        return value
    for file in files:layouts[file]=transform(layouts[file],file)
    # Existing pack emotes keep voice verification even if their deck panels are disabled.
    for target,label in emote_catalog.items():
        name=command_text(target.rsplit('.',1)[-1],label)[0]
        c=existing.get(name);actions=(c or {}).get('actions') or []
        if c and actions and _sync_matches(actions[-1],name,skill):
            cmdmeta.setdefault(name,{'buttons':[],'effects':[]}).update(
                emote=target,label=label,no_key=not emotes.verified(c,target,idx))
    # Explicit set commands must also sync even where only the toggle has a button.
    for name,effects in effect_cache.items():
        if effects:cmdmeta.setdefault(name,{'buttons':[],'effects':effects})
    # Pair from live action resolution only, never names, recorded intent or macros.
    absolute = {}
    for name,c in existing.items():
        reached=sorted({ident.rsplit('.',1)[-1] for ident in core.resolved(c,idx)})
        if name in MACROS or len(reached)!=1 or len(core.events(c))!=1:continue
        for group,value in EFFECTS.get(reached[0].rsplit('.',1)[-1],[]):
            if type(value) is bool:
                candidates=absolute.setdefault(group,{})
                rank=(len(EFFECTS.get(reached[0],[])),name)
                if value not in candidates or rank<candidates[value]:candidates[value]=rank
    for layout in layouts.values():
        for b in walk(layout):
            state=b.get('state') or {}; pair=absolute.get(state.get('group'),{})
            if b.get('kind')=='command' and state.get('set','absent') is None and True in pair and False in pair:
                b['pair']={'on':pair[True][1],'off':pair[False][1]}
                buttons[b['id']]['pair']=dict(b['pair'])
    entries=[]
    added_names={c['name'] for c in added}
    for name in sorted(cmdmeta):
        c=existing[name]
        target_actions=copy.deepcopy(c.get('actions') or [])
        if not target_actions or not _sync_matches(target_actions[-1],name,skill):
            target_actions.append(sync_step(name,skill))
            if name not in added_names:updates.append({'name':name,'actions':target_actions})
        merged={**c,'actions':target_actions}
        reached=core.resolved(merged,idx,curated=True)
        emote=cmdmeta[name].get('emote')
        if emote:
            reached=[emote] if emotes.verified(merged,emote,idx) else []
        entries.append({'name':name,'expectedActions':reached,'baselineActions':reached,
                        'emote':emote, 'activationClasses':sorted({r.get('activationMode','') for e in core.events(merged)
                           for r,_ in core.matches(e,merged,idx,True)}),
                        'fingerprint':core.command_fingerprint(merged),'baseline':False,'verifiable':bool(reached), 'owned_emote':bool(emote and reached)})
    # Drop empty emote panels when disabled, preserving other geometry.
    for layout in layouts.values():
        if not settings.get('emotes',True):
            if 'panels' in layout:
                for key in list(layout['panels']):
                    if key.startswith('emotes'):del layout['panels'][key]
                for region in layout['regions']:region['panels']=[p for p in region['panels'] if p in layout['panels']]
            else:layout['pages']=[p for p in layout['pages'] if p['buttons']]
    groups=sorted({e['group'] for m in cmdmeta.values() for e in m['effects']})
    return {'binds_to_replace':replacements,'binds_to_add':binds,'commands_to_add':added,'commands_to_update':updates,
            'manifest_entries':entries,'deck':{'version':'1.2.1','buttons':buttons,
            'commands':dict(sorted(cmdmeta.items())),'groups':groups,
            'layouts':{'tablet':layouts['hud.json'],'phone':layouts['hud-phone.json'], 'files':layouts},
            'decisions':decisions},'warnings':sorted(set(warnings))}
