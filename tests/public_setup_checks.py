"""Fresh-player opt-in acceptance using the genuine Wingman facade and schema."""
import asyncio
import copy
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock, patch
from sample_index import synthetic_index

def qualify(module,facade,config):
    from api.interface import CommandConfig
    from sc_voice_cockpit.keybindings import keybinds_core as core
    from sc_voice_cockpit.deck.runtime import DeckRuntime
    from sc_voice_cockpit.deck.setup import SETUP_CARD
    path=os.environ.get('SC_DEFAULTS')
    index=json.loads(Path(path).read_text(encoding='utf-8')) if path else synthetic_index()
    with tempfile.TemporaryDirectory(prefix='cockpit-fresh-player-') as temp:
        directory=Path(temp);generated=directory/'generated';generated.mkdir()
        profile=directory/'actionmaps.xml'
        profile.write_bytes(b'<ActionMaps>\r\n <ActionProfiles>\r\n </ActionProfiles>\r\n</ActionMaps>\r\n')
        before=profile.read_bytes();before_mtime=profile.stat().st_mtime_ns
        raw=NS(name='Synthetic Pilot',config=NS(commands=[CommandConfig(name='Existing user command',actions=[{'wait':.1}])]))
        raw.command_executor=NS(get_command=lambda name:next((c for c in raw.config.commands if c.name==name),None))
        before_commands=copy.deepcopy([core.command_dict(c) for c in raw.config.commands])
        saves=[];raw.tower=NS(save_wingman_commands=lambda name:saves.append(True) or True)
        wing=NS(name=raw.name,config=NS(record_key='right ctrl+f1',record_mouse_button='x2'),commands=facade.SkillCommands(raw),
                ui=NS(show_dialog=AsyncMock()),tools=NS(invoke=AsyncMock()))
        cfg=config.model_copy(deep=True)
        obj=module.SC_VoiceCockpit(config=cfg,settings=NS(cancel_tts_key='shift+x',voice_activation=NS(mute_toggle_key='shift+y')),wingman=wing)
        obj.get_generated_files_dir=lambda:str(generated);obj._core=core
        obj.log=NS(info=lambda *a,**k:None,warning=lambda *a,**k:None)
        obj._process_check=lambda:False;obj._deck=DeckRuntime(obj,focus=lambda:True)
        live=core.LiveBindings(index,profile);live.refresh();obj._bindings=live
        obj._start_refresh=Mock();obj._closed=False
        async def integrate():
            obj._loop=asyncio.get_running_loop()
            await asyncio.to_thread(obj._integrate_pack,live)
        assert not obj.deck_setup_enabled()
        asyncio.run(integrate())
        assert profile.read_bytes()==before and profile.stat().st_mtime_ns==before_mtime and not saves
        assert [core.command_dict(c) for c in raw.config.commands]==before_commands
        assert not (generated/'custom_binds.json').exists()
        assert not (generated/'command_manifest.json').exists()
        unavailable=[b for b in obj._deck.manifest['buttons'].values() if b.get('not_set_up')]
        assert len(unavailable)>0
        sent=[];obj._deck.clients['client']=None;obj._deck.send=lambda client,msg:sent.append(msg)
        for kind in ('command','hold'):
            button=next(b for b in unavailable if b['kind']==kind)
            obj._deck.receive('client',{'t':'down' if kind=='hold' else 'tap','id':button['id']})
            assert sent[-1]['t']=='not-set-up' and obj._deck.jobs.empty() and not obj._deck.held
        wing.tools.invoke.assert_not_awaited()
        pairing=NS(pairing_url='http://127.0.0.1:7869/',pairing_code='synthetic')
        obj._ui_server=pairing
        with patch.object(module.asyncio,'sleep',new_callable=AsyncMock) as delay, patch('sc_voice_cockpit.navpoint.navpoint_ui.sharing.qr_png_base64',return_value=None):
            asyncio.run(obj._announce_ui_startup(pairing));delay.assert_awaited_once_with(15)
            assert SETUP_CARD in wing.ui.show_dialog.call_args.args[1]
            asyncio.run(obj._announce_ui_startup(pairing))
            assert SETUP_CARD not in wing.ui.show_dialog.call_args.args[1]
        def setting(value):
            changed=obj.config.model_copy(deep=True)
            next(p for p in changed.custom_properties if p.id=='deck_setup').value=value
            return changed
        # The public update hook requests one refresh; drive that refresh inertly here.
        asyncio.run(obj.update_config(setting(True)))
        obj._start_refresh.assert_called_once()
        obj._process_check=lambda:True
        asyncio.run(integrate())
        assert profile.read_bytes()==before and len(raw.config.commands)==1 and not saves
        assert json.loads((generated/'deck_pending.json').read_text(encoding='utf-8'))['pending']
        obj._process_check=lambda:False
        asyncio.run(integrate())
        count=len(raw.config.commands)-1
        binds=len(json.loads((generated/'custom_binds.json').read_text(encoding='utf-8')))
        assert count>0 and binds>0 and len(saves)==1 and profile.read_bytes()!=before
        backups=list((generated/'backups').glob('*'))
        assert backups and any('actionmaps' in p.name for p in backups)
        assert any('commands' in p.name for p in backups)
        stable_profile=profile.read_bytes();stable_commands=copy.deepcopy([core.command_dict(c) for c in raw.config.commands])
        asyncio.run(integrate());assert len(saves)==1
        asyncio.run(obj.update_config(setting(False)))
        assert not obj.deck_setup_enabled()
        asyncio.run(integrate())
        assert profile.read_bytes()==stable_profile and [core.command_dict(c) for c in raw.config.commands]==stable_commands and len(saves)==1
        # Persisted off must win even with an existing pack on the next skill instance.
        restarted=module.SC_VoiceCockpit(config=obj.config,settings=NS(),wingman=wing)
        restarted.get_generated_files_dir=lambda:str(generated)
        assert not restarted.deck_setup_enabled()
        return {'index':'local game defaults' if path else 'synthetic', 'off_commands_added':0,'off_commands_updated':0,
                'off_profile_writes':0,'not_set_up_buttons':len(unavailable),'card_once_15s':True,
                'on_commands_added':count,'on_binds_added':binds,'command_saves':len(saves),
                'profile_and_command_backups':True,'running_game_deferred':True,'off_again_unchanged':True,
                'restart_remains_off':True}
