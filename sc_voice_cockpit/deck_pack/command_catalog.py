"""Human-facing command text keyed by verified SC action, not panel shorthand.

The tablet keeps its compact labels. Wingman's tool enum gets unambiguous names
and one-sentence descriptions; existing commands are never renamed.
"""

# These open keyboard-focus-taking menus. Named tablet dispatch and exact
# instant phrases still work; accidental AI selection must not open them.
FOCUS_ACTIONS = frozenset({
    'ui_toggle_pause', 'toggle_chat', 'toggle_contact', 'v_starmap',
    'mobiglas', 'pc_pit_inventory',
})

COMMAND_TEXT = {
    'v_atc_request': ('Request Landing Clearance', 'Request takeoff or landing clearance from air traffic control.'),
    'toggle_chat': ('Open Chat', 'Open or close the in-game chat window.'),
    'toggle_contact': ('Open Contacts', 'Open or close the contacts window.'),
    'v_starmap': ('Open Star Map', 'Open or close the star map.'),
    'mobiglas': ('Open MobiGlas', 'Open or close mobiGlas.'),
    'v_toggle_landing_system': ('Toggle Landing Gear', 'Deploy or retract the landing gear.'),
    'v_vtol_toggle': ('Toggle VTOL', 'Switch the thrusters between VTOL and normal flight orientation.'),
    'v_autoland': ('Engage Auto Land', 'Engage automatic landing at an available landing pad.'),
    'v_toggle_docking_request': ('Request Docking', 'Send a docking request to the targeted ship.'),
    'v_dock_toggle_view': ('Toggle Docking Camera', 'Switch the docking camera view.'),
    'v_engineering_assignment_reset': ('Reset Power Distribution', 'Reset power distribution between weapons, thrusters and shields.'),
    'v_power_toggle': ('Toggle Ship Power', 'Turn ship power on or off.'),
    'v_power_toggle_weapons': ('Toggle Weapons Power', 'Turn power to the weapons on or off.'),
    'v_power_toggle_thrusters': ('Toggle Thrusters Power', 'Turn power to the thrusters on or off.'),
    'v_power_toggle_shields': ('Toggle Shields Power', 'Turn power to the shields on or off.'),
    'v_flightready': ('Set Flight Ready', 'Power up the ship systems for flight.'),
    'v_master_mode_cycle_long': ('Cycle Master Mode', 'Switch between SCM and NAV master modes.'),
    'v_toggle_qdrive_engagement': ('Engage Quantum Drive', 'Engage or disengage quantum travel.'),
    'v_master_mode_set_scm': ('SCM Master Mode', 'Set the ship to SCM master mode.'),
    'v_master_mode_set_nav': ('NAV Master Mode', 'Set the ship to NAV master mode.'),
    'v_ifcs_speed_limiter_toggle': ('Toggle Speed Limiter', 'Turn the flight speed limiter on or off.'),
    'v_ifcs_toggle_esp': ('Toggle ESP', 'Turn Enhanced Stick Precision on or off.'),
    'v_ifcs_toggle_gforce_safety': ('Toggle G-Force Safety', 'Turn the flight g-force safety limiter on or off.'),
    'v_lights': ('Toggle Ship Lights', 'Turn the ship lights on or off.'),
    'pl_exit': ('Exit Seat', 'Leave the current seat.'),
    'v_shield_reset_level': ('Reset Shield Levels', 'Reset the shield faces to an even distribution.'),
    'v_salvage_cycle_modifiers_left': ('Cycle Left Salvage Modifier', 'Cycle the modifier on the left salvage head.'),
    'v_salvage_cycle_modifiers_right': ('Cycle Right Salvage Modifier', 'Cycle the modifier on the right salvage head.'),
    'v_salvage_decrease_beam_spacing': ('Decrease Salvage Beam Spacing', 'Move the salvage beams closer together.'),
    'v_salvage_increase_beam_spacing': ('Increase Salvage Beam Spacing', 'Move the salvage beams farther apart.'),
    'v_toggle_mining_laser_fire': ('Toggle Mining Laser', 'Turn the mining laser on or off.'),
    'v_toggle_mining_laser_type': ('Toggle Mining Laser Mode', 'Switch the mining laser between fracture and extraction modes.'),
    'v_weapon_countermeasure_decoy_launch': ('Launch Countermeasure Decoy', 'Launch a decoy countermeasure.'),
    'v_weapon_countermeasure_noise_launch': ('Launch Countermeasure Noise', 'Deploy a noise countermeasure.'),
    'v_weapon_countermeasure_decoy_launch_panic': ('Launch Panic Decoys', 'Launch a burst of decoy countermeasures.'),
    'v_weapon_toggle_launch_missile': ('Launch Missiles', 'Launch missiles only while in missile operator mode.'),
    'v_weapon_decrease_max_missiles': ('Decrease Missile Salvo', 'Reduce the number of missiles in the next salvo.'),
    'v_weapon_pip_toggle_lead_lag': ('Toggle Lead or Lag PIPs', 'Switch the aiming indicators between lead and lag PIPs.'),
    'v_toggle_all_doors': ('Toggle All Doors', 'Open or close all ship doors.'),
    'v_open_all_doors': ('Open All Doors', 'Open all ship doors.'),
    'v_close_all_doors': ('Close All Doors', 'Close all ship doors.'),
    'v_lock_all_doors': ('Lock All Doors', 'Lock all ship doors.'),
    'v_unlock_all_doors': ('Unlock All Doors', 'Unlock all ship doors.'),
    'pc_pit_inventory': ('Open Inventory', 'Open or close the personal inventory.'),
    'v_view_cycle_fwd': ('Cycle Camera View', 'Cycle between first-person and third-person camera views.'),
    'walk': ('Toggle Walking', 'Switch between walking and running movement.'),
    'drop': ('Drop Held Item', 'Drop the item currently held.'),
    'inspect': ('Inspect Held Item', 'Inspect the item currently held.'),
    'ui_toggle_pause': ('Press Escape', 'Press Escape to open or close the pause menu.'),
}

for _action, _mode in (
    ('flight', 'Flight'), ('guns', 'Guns'), ('missile', 'Missile'),
    ('quantum', 'Quantum'), ('scan', 'Scanning'), ('mining', 'Mining'),
    ('salvage', 'Salvage'),
):
    COMMAND_TEXT['v_set_'+_action+'_mode'] = (
        _mode+' Operator Mode', 'Set the ship to '+_mode.lower()+' operator mode.')

for _part, _system in (('weapons', 'Weapons'), ('engine', 'Thrusters'), ('shields', 'Shields')):
    for _suffix, _verb, _description in (
        ('max', 'Max', 'Allocate maximum power to the '),
        ('min', 'Min', 'Allocate minimum power to the '),
        ('increase', 'Increase', 'Increase power allocated to the '),
        ('decrease', 'Decrease', 'Decrease power allocated to the '),
    ):
        COMMAND_TEXT['v_engineering_assignment_'+_part+'_'+_suffix] = (
            _verb+' Power to '+_system, _description+_system.lower()+'.')

for _direction, _face in (
    ('up', 'Top'), ('forward', 'Front'), ('down', 'Bottom'),
    ('left', 'Left'), ('right', 'Right'), ('back', 'Rear'),
):
    COMMAND_TEXT['v_shield_raise_level_'+_direction] = (
        'Raise '+_face+' Shields', 'Increase the shield level on the '+_face.lower()+' face.')

for _kind, _name, _description in (
    ('hostile', 'Previous Hostile Target', 'Select the previous hostile target.'),
    ('attacker', 'Previous Attacker', 'Select the previous target attacking you.'),
    ('friendly', 'Previous Friendly Target', 'Select the previous friendly target.'),
    ('all', 'Previous Target', 'Select the previous target of any affiliation.'),
    ('subitem', 'Previous Subtarget', 'Select the previous subtarget on the targeted ship.'),
    ('in_view', 'Previous Target in View', 'Select the previous target in your view.'),
):
    COMMAND_TEXT['v_target_cycle_'+_kind+'_back'] = (_name, _description)

for _number in range(1, 4):
    COMMAND_TEXT['v_mining_use_consumable'+str(_number)] = (
        'Mining Module '+str(_number), 'Activate mining module '+str(_number)+'.')
    COMMAND_TEXT['v_enter_remote_turret_'+str(_number)] = (
        'Enter Remote Turret '+str(_number), 'Take control of remote turret '+str(_number)+'.')


def command_text(action, label):
    if action.startswith('emote_'):
        return 'Emote '+label.title(), 'Play the '+label.lower()+' emote.'
    # Fail explicitly if a future catalog action needs editorial text, rather
    # than exposing ambiguous tablet labels in the AI command enum again.
    return COMMAND_TEXT[action]
