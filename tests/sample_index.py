"""Synthetic test-only controls, constructed from the MIT deck catalog.

This is not a game extract or a recommended player key chart. Use an actual
locally extracted index for game-version acceptance; never distribute that file.
"""
from sc_voice_cockpit.deck_pack import planner

def synthetic_index():
    rows={}
    for layout in planner.LAYOUTS.values():
        for button in planner.walk(layout):
            _,reason=planner._target(button,[])
            if not reason.startswith('Action not found in supplied index: '):continue
            action=reason.split(': ',1)[1]
            if action=='None':continue
            candidates=[name.rsplit('.',1)[0] for name in planner.PROFILE if name.endswith('.'+action)]
            group=('player_emotes' if action.startswith('emote_') else
                   'spaceship_missiles' if action=='v_weapon_toggle_launch_missile' else
                   candidates[0] if candidates else button.get('actionmap') or
                   ('default' if action in ('mobiglas','toggle_chat','toggle_contact','ui_toggle_pause') else 'spaceship_movement'))
            rows[(group,action)]={'map':group,'name':action,'label':button.get('label',action),
                'mapLabel':'Synthetic controls','description':'Synthetic test action',
                'keyboard':[],'mouse':[],'joystick':[],'gamepad':[]}
    for action,key in [('v_vtol_toggle','k'),('v_vtol_on','ralt+z'),('v_vtol_off','lalt+z'),
                       ('v_toggle_landing_system','n'),('v_flightready','ralt+r')]:
        row=next((r for (_,a),r in rows.items() if a==action),None)
        if row is None:
            row={'map':'spaceship_movement','name':action,'label':action,'mapLabel':'Synthetic controls',
                 'description':'Synthetic test action','keyboard':[],'mouse':[],'joystick':[],'gamepad':[]}
            rows[(row['map'],action)]=row
        row['keyboard']=[{'input':'kb1_'+key}]
    return {'actions':list(rows.values())}
