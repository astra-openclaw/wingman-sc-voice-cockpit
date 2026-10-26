"""Pack-owned emote migration and conservative key verification."""
from __future__ import annotations
import copy
import re
from . import compat as core

UNSENDABLE = frozenset(('insert','delete','home','end','pgup','pgdn','up','down','left','right'))
LEGACY = dict(zip(('agree','angry','atease','attention','blah','bored','bow','burp','cheer','wave','sleep'),
                  ('np_1','np_2','np_3','np_4','np_5','np_6','np_7','np_8','np_9','np_0','pgup')))


def sendable(key):
    return all(not t.startswith('np_') and t not in UNSENDABLE for t in core.normalize_key(key).split('+'))


def verified(command, target, index):
    """Exactly one real emote action, faithful key steps and no ambiguous chord."""
    from .planner import key_steps, sync_step
    events = core.events(command)
    if len(events) != 1 or not sendable(events[0][0]):
        return False
    key, hold, _ = events[0]
    actions = command.get('actions') or []
    # Our split right-Ctrl scan-code form must survive editing/schema expansion.
    expected = key_steps(key, hold)
    if actions and (actions[-1].get('skill_action') or {}).get('function_name') == 'deck_sync':
        expected += [sync_step(command['name'], 'SC_VoiceCockpit')]
    if core.command_fingerprint(command) != core.command_fingerprint({'actions': expected}):
        return False
    return core.resolved(command, index) == [target]


def migrate(index, profile_root, custom, commands, occupied, pool, catalog, skill):
    """Only exact pack-generated commands AND exact owned profile slots migrate."""
    from .planner import key_steps, sync_step
    from .command_catalog import command_text
    replacements, updates = [], []
    custom_by = {(r['map'], r['action']):r for r in custom}
    for suffix, base in LEGACY.items():
        action = 'emote_'+suffix
        target = 'player_emotes.'+action
        label = catalog.get(target)
        if not label:
            continue
        name = 'Emote '+label.title()
        command = commands.get(name)
        old = 'kb1_rctrl+'+base
        owned = custom_by.get(('player_emotes', action), {})
        actual = [b.get('input') for m in profile_root.iter('actionmap') if m.get('name')=='player_emotes'
                  for a in m.findall('action') if a.get('name')==action for b in a.findall('rebind')
                  if core._slot(b.get('input',''))[:2]==('keyboard',1)]
        original = {'actions':key_steps(old)+[sync_step(name,skill)]}
        if (not command or command.get('description')!=command_text(action,label)[1]
                or command.get('instant_activation')!=['emote '+label.lower()]
                or command.get('force_instant_activation') is not True
                or core.command_fingerprint(command)!=core.command_fingerprint(original)):
            continue
        if owned.get('cleared') or owned.get('removed'):
            continue
        recovery = owned.get('deck_rebind_from') == old and actual == [owned.get('input')] and sendable(owned['input'])
        if not recovery and not (owned.get('input')==old and actual==[old]):
            continue
        key = core.normalize_key(owned['input']) if recovery else next((k for k in pool if k not in occupied),None)
        if key is None:
            raise ValueError('No free plain key for pack emote')
        occupied.add(key)
        if not recovery:
            replacements.append({'map':'player_emotes','action':action,'before':old,
                                 'input':'kb1_'+key,'command':name})
        updates.append({'name':name,'actions':key_steps(key)+[sync_step(name,skill)]})
        commands[name] = {**command,'actions':updates[-1]['actions']}
        row = next(r for r in index.actions if r['map']=='player_emotes' and r['name']==action)
        for bindings in index.command_inputs.values():
            bindings[:] = [(r,b) for r,b in bindings if r is not row]
        row['keyboard'] = [{'input':'kb1_'+key,'activationMode':row.get('activationMode','')}]
        index.command_inputs.setdefault(key,[]).append((row,row['keyboard'][0]))
    return replacements, updates


def replace_profile(live, directory, changes, custom, process_check, closed):
    """Guarded byte-preserving replacement; caller owns command-save rollback."""
    from ..keybindings import self_heal as healer
    def running():
        try: return bool(process_check())
        except Exception: return True
    if running():
        return {'deferred':[r['map']+'.'+r['action'] for r in changes]}, None
    before = live.path.read_bytes()
    text = before.decode('utf-8-sig')
    for row in changes:
        # Restrict replacement to one exact action in one exact map. No XML reserialization.
        maps = list(re.finditer(r'<actionmap\b[^>]*name=["\']'+re.escape(row['map'])+r'["\'][^>]*>.*?</actionmap>',text,re.S))
        candidates = []
        for m in maps:
            for a in re.finditer(r'<action\b[^>]*name=["\']'+re.escape(row['action'])+r'["\'][^>]*>.*?</action>',m.group(),re.S):
                candidates.append((m.start()+a.start(),m.start()+a.end(),a.group()))
        if len(candidates)!=1:
            raise ValueError('Owned emote profile shape changed')
        start,end,body=candidates[0]
        pattern=r'(<rebind\b[^>]*\binput=["\'])'+re.escape(row['before'])+r'(["\'])'
        body,count=re.subn(pattern,lambda m:m[1]+row['input']+m[2],body)
        if count!=1:
            raise ValueError('Owned emote binding changed')
        text=text[:start]+body+text[end:]
    data=(b'\xef\xbb\xbf' if before.startswith(b'\xef\xbb\xbf') else b'')+text.encode('utf-8')
    healer.profile_root(text)
    updated=copy.deepcopy(custom)
    for change in changes:
        row=next(r for r in updated if (r['map'],r['action'],r['input'])==
                 (change['map'],change['action'],change['before']))
        row.update(input=change['input'],deck_rebind_from=change['before'])
    path=directory/'custom_binds.json';custom_before=path.read_bytes()
    healer.backup(directory,'actionmaps.xml',before,closed)
    healer.backup(directory,'custom-binds.json',custom_before,closed)
    healer.atomic_bytes(live.path,data,before,running,closed)
    try:healer.atomic_json(path,updated,closed)
    except BaseException:
        healer.atomic_bytes(live.path,before,data,running,closed)
        raise
    if live.path.read_bytes()!=data:
        raise ValueError('Emote rebind readback failed')
    live.refresh()
    return {}, (before,data,custom_before,path.read_bytes())
