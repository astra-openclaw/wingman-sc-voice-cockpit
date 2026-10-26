"""Hand-authored, synthetic controls, not extracted or redistributed game data."""
import copy

def binding(key,mode='',**extra):
    return dict(input=key,activationMode=mode,**extra)

def mini_index():
    rows=[]
    def add(m,name,label,key='',mode='',device='keyboard',**extra):
        row=dict(map=m,mapLabel={'spaceship_general':'Vehicles - Cockpit','spaceship_power':'Flight Power'}.get(m,m.replace('_',' ').title()),name=name,label=label,description='',activationMode=mode,keyboard=[],mouse=[],gamepad=[],joystick=[])
        if key:row[device]=[binding(key,mode,**extra)]
        rows.append(row)
    # Everyday queries and vocabulary targets. All key values below are test inventions.
    add('spaceship_general','v_flightready','Flight Systems Ready','ralt+r')
    add('spaceship_general','v_lock_all_doors','Lock All Doors')
    add('spaceship_general','v_toggle_all_doorlocks','Lock/Unlock Doors (Toggle)')
    add('spaceship_general','v_open_all_doors','Open All Doors')
    add('spaceship_general','v_toggle_all_doors','Open/Close Doors')
    add('spaceship_general','v_toggle_all_portlocks','Port Lock Toggle All','ralt+k')
    add('seat_general','v_toggle_all_portlocks','Port Lock Toggle All','ralt+k')
    add('spaceship_general','v_toggle_landing_system','Landing Gear (Toggle)','n')
    add('spaceship_general','v_landing_gear_down','Landing Gear Down')
    add('spaceship_general','v_landing_gear_up','Landing Gear Up')
    add('spaceship_defensive','v_weapon_countermeasure_decoy_launch','Launch Decoy (Tap) / Set and Launch Decoy (Hold)','h')
    add('spaceship_defensive','v_weapon_countermeasure_noise_launch','Deploy Noise','j')
    add('spaceship_quantum','v_toggle_qdrive_engagement','Engage Quantum Drive','mouse1','hold',device='mouse',holdTriggerDelay='0.5')
    add('spaceship_movement','v_ifcs_throttle_swap_mode','Cruise Control','c')
    add('spaceship_targeting_advanced','v_target_cycle_hostile_reset','Target Closest Hostile','t')
    add('spaceship_missiles','v_weapon_toggle_launch_missile','Launch Missiles','mouse1',device='mouse')
    add('default','pl_exit','Exit Seat','y')
    add('spaceship_radar','v_invoke_ping','Radar Ping','tab')
    add('spaceship_weapons','v_weapon_gimbals_state_toggle','Gimbal State Toggle Locked / Unlocked','g','press')
    add('spaceship_weapons','v_weapon_preset_attack','Weapon Preset Fire','mouse1',device='mouse')
    add('spaceship_scanning','v_scanning_trigger_scan','Activate Scanning','mouse1',device='mouse')
    add('spaceship_view','v_view_freelook_mode','Freelook','z')
    add('spaceship_movement','v_strafe_up','Strafe Up','space')
    add('seat_general','v_toggle_salvage_mode','Salvage Operator Mode','m')
    add('lights_controller','v_lights','Headlights','l')
    add('spaceship_view','v_light_amplification_toggle','Light Amplification','lalt+l')
    add('spaceship_power','v_power_set_thrusters_off','Set Engines Power Off')
    add('spaceship_power','v_power_set_thrusters_on','Set Engines Power On')
    add('spaceship_power','v_power_toggle_thrusters','Toggle Power - Thrusters','i')
    add('spaceship_power','v_power_toggle_shields','Toggle Power - Shields','o')
    add('spaceship_power','v_power_set_shields_on','Set Shields Power On')
    add('spaceship_power','v_power_set_shields_off','Set Shields Power Off')
    add('spaceship_power','v_power_throttle_max','Engines - Set to Max','f6','hold',holdTriggerDelay='0.5')
    add('spaceship_power','v_engineering_assignment_shields_max','Shields - Set to Max','f7','hold',holdTriggerDelay='0.5')
    add('spaceship_power','v_power_throttle_up','Engines - Increase','f6','tap',releaseTriggerThreshold='0.3')
    add('spaceship_movement','v_afterburner','Boost','lshift')
    add('spaceship_movement','v_afterburner_hold','Boost','lshift','hold')
    add('salvage_mode','v_salvage_gimbal','Salvage Gimbal Reset','g')
    add('player','prone','Prone','x')
    add('player','crouch','Crouch','lctrl')
    add('player','jump','Jump','space')
    add('player','flashlight','Flashlight','t')
    add('player','inventory','Inventory','i')
    add('player','helmet','Helmet Toggle','h')
    add('view_director_mode','camera_save','Save Camera View','np_1')
    add('view_director_mode','camera_next','Next Camera View','f4')
    add('spectator','v_strafe_up','Strafe Up','u')
    add('debug','v_debug','Flight Ready Debug','ralt+r')
    add('server_test','v_server','Server Flight Ready','ralt+r')
    add('flycam','v_flycam','Free Camera','z')
    add('ui_textfield','v_paste','Paste','lctrl+v')
    add('character_customizer','v_rotate','Rotate Character','mouse1',device='mouse')
    # Additional synthetic controls widen search, device and context coverage.
    for m,name,label,key in [
        ('spaceship_movement','v_strafe_left','Strafe Left','a'),('spaceship_movement','v_strafe_right','Strafe Right','d'),
        ('spaceship_movement','v_strafe_down','Strafe Down','lctrl'),('spaceship_movement','v_roll_left','Roll Left','q'),
        ('spaceship_movement','v_roll_right','Roll Right','e'),('spaceship_movement','v_throttle_up','Throttle Up','w'),
        ('spaceship_movement','v_throttle_down','Throttle Down','s'),('spaceship_targeting','v_target_next','Target Next','5'),
        ('spaceship_targeting','v_target_previous','Target Previous','4'),('spaceship_general','v_eject','Eject','ralt+e'),
        ('spaceship_general','v_exit','Exit Vehicle','y'),('spaceship_general','v_horn','Horn','b'),
        ('spaceship_weapons','v_weapon_mode','Weapon Mode','v'),('spaceship_missiles','v_missile_next','Next Missile','r'),
        ('spaceship_power','v_power_reset','Reset Power Distribution','f8'),('turret_main','v_turret_fire','Turret Fire','mouse1'),
        ('turret_main','v_turret_stabilize','Turret Stabilization','s'),('player','v_reload','Reload','r'),
        ('player','v_interact','Interact','f'),('player','v_medical','Medical Tool','6'),
        ('player','v_sprint','Sprint','lshift'),('player','v_walk','Walk','capslock'),
        ('player','v_lean_left','Lean Left','q'),('player','v_lean_right','Lean Right','e'),
        ('spaceship_mining','v_mining_laser','Mining Laser','mouse1'),('spaceship_mining','v_mining_mode','Mining Mode','m'),
        ('spaceship_general','v_request_landing','Request Landing','lalt+n'),('spaceship_view','v_cycle_view','Cycle View','f4')]:
        add(m,name,label,key,device='mouse' if key.startswith('mouse') else 'keyboard')
    assert len(rows)==80,len(rows)
    return dict(metadata=dict(buildId='synthetic',extractorVersion=2),actions=copy.deepcopy(rows))

def commands():
    return [dict(name='Prepare Ship',actions=[dict(keyboard=dict(hotkey='ralt+r',hold=0.1))]),
            dict(name='Open Inventory',actions=[dict(keyboard=dict(hotkey='i',hold=0.1))]),
            dict(name='Reset History',actions=None),dict(name=None,actions=[None,dict(mouse=None,keyboard=None)])]
