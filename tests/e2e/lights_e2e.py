"""Light-only repair across two real browser clients; synthetic data only."""
import base64
import json
from run_e2e import Chrome,request

def run(base,token,artifacts=None):
    results=[];measurements=[]
    def passed(name):
        results.append(name);print(name+" PASS",flush=True)
    def events():return request(base,'/__test/events')
    with Chrome() as a, Chrome() as b:
        for browser in (a,b):
            browser.navigate(base+'/?t='+token)
            browser.wait(lambda:browser.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
            if browser.js("!document.querySelector('#intro').classList.contains('gone')"):
                browser.click('#intro')
                browser.wait(lambda:browser.js("document.querySelector('#intro').classList.contains('gone')"))
            browser.js("document.querySelector('.js-settings, #themeBtn').click()")
            browser.wait(lambda:browser.js("document.querySelector('#picker').classList.contains('open') && document.querySelectorAll('[data-light-group]').length>0"))
        layout=request(base,'/layout.json?t='+token)
        def groups(value):
            found=set()
            if isinstance(value,dict):
                if value.get('state',{}).get('group'):found.add(value['state']['group'])
                for child in value.values():found.update(groups(child))
            elif isinstance(value,list):
                for child in value:found.update(groups(child))
            return found
        expected=sorted(groups(layout))
        for browser in (a,b):
            assert browser.js("[...document.querySelectorAll('[data-light-group]')].map(e=>e.dataset.lightGroup).sort()")==expected
        passed('settings groups exactly match current layout')

        selector='[data-light-group="vtol"]'
        request(base,'/__test/push',{'t':'state','group':'vtol','on':False})
        for browser in (a,b):
            browser.wait(lambda:browser.js("document.querySelector('[data-light-group=vtol]').getAttribute('aria-checked')==='false'"))
        before=events()
        a.js("document.querySelector('[data-light-group=vtol]').scrollIntoView({block:'center'})")
        a.click(selector)
        for browser in (a,b):
            browser.wait(lambda:browser.js("document.querySelector('[data-light-group=vtol]').getAttribute('aria-checked')==='true' && document.querySelector('[data-state-group=vtol]').classList.contains('fx-on')"))
        after=events()
        messages=[m for m in after['events'][len(before['events']):] if m.get('t')!='hb']
        assert messages==[{'t':'set-state','group':'vtol','on':True}],messages
        assert after['invocations']==before['invocations']
        passed('one set-state updates two tablets without command or key')

        a.click('#closePicker')
        before=events()
        a.click('[data-state-group="vtol"]')
        a.wait(lambda:len(events()['invocations'])==len(before['invocations'])+1)
        assert events()['invocations'][-1]=='VTOL Off'
        for browser in (a,b):
            browser.wait(lambda:not browser.js("document.querySelector('[data-state-group=vtol]').classList.contains('fx-on')"))
        messages=[m for m in events()['events'][len(before['events']):] if m.get('t')!='hb']
        assert len(messages)==1 and messages[0]['t']=='tap',messages
        passed('paired tap sends normal tap and explicit command syncs both lights')
        a.click('[data-state-group="vtol"]')
        a.wait(lambda:events()['invocations'][-1]=='VTOL On')
        passed('second paired tap uses explicit on')

        def capture(name):
            if artifacts:
                artifacts.mkdir(parents=True,exist_ok=True)
                (artifacts/(name+'.png')).write_bytes(base64.b64decode(a.call('Page.captureScreenshot',{'format':'png'})['data']))
        a.js("document.querySelector('.js-settings, #themeBtn').click()")
        for device,w,h in [('tablet',1200,900),('phone',852,393)]:
            a.call('Emulation.setDeviceMetricsOverride',{'width':w,'height':h,'deviceScaleFactor':1,'mobile':device=='phone'})
            if device=='phone':
                a.navigate(base+'/?device=phone&t='+token)
                a.wait(lambda:a.js("document.documentElement.dataset.device==='phone' && !document.body.classList.contains('offline')"))
                if a.js("!document.querySelector('#intro').classList.contains('gone')"):
                    a.click('#intro');a.wait(lambda:a.js("document.querySelector('#intro').classList.contains('gone')"))
                a.js("document.querySelector('.js-settings, #themeBtn').click()")
            for theme,large,contrast in [('rsi',False,False),('origin',True,False),('rsi',True,True)]:
                a.js("document.documentElement.dataset.theme="+json.dumps(theme)+";document.documentElement.toggleAttribute('data-large',"+json.dumps(large)+");document.documentElement.toggleAttribute('data-contrast',"+json.dumps(contrast)+")")
                a.js("document.querySelector('#fixLights').scrollIntoView({block:'start'})")
                a.js("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
                metrics=a.js("""(()=>{
                  const box=document.querySelector('#fixLights'),s=getComputedStyle(box);
                  const rows=[...box.querySelectorAll('button')].map(e=>{const r=e.getBoundingClientRect();return {w:r.width,h:r.height,overflow:e.scrollWidth>e.clientWidth,
                    transition:getComputedStyle(e.querySelector('.sw-knob'),'::after').transitionDuration}});
                  const fg=getComputedStyle(box.querySelector('b')).color,bg=s.backgroundColor;
                  return {rows,fg,bg,label:getComputedStyle(box.querySelector('.sw-text b')).fontSize};
                })()""")
                assert all(r['w']>=44 and r['h']>=44 and not r['overflow'] and r['transition']=='0s' for r in metrics['rows']),metrics
                def lum(rgb):
                    values=[float(x)/255 for x in rgb.removeprefix('rgb(').removesuffix(')').split(',')]
                    linear=[x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4 for x in values]
                    return sum(x*y for x,y in zip(linear,[.2126,.7152,.0722]))
                # Body text uses text/panel tokens; measure actual row styles.
                colors=a.js("(()=>{const e=document.querySelector('.fix-light');return [getComputedStyle(e.querySelector('.sw-text b')).color,getComputedStyle(e).backgroundColor]})()")
                l1,l2=sorted([lum(c) for c in colors]);ratio=(l2+.05)/(l1+.05)
                assert ratio>=4.5,(device,theme,ratio)
                measurements.append({'device':device,'theme':theme,'large':large,'contrast':contrast,'contrast_ratio':ratio,'minimum_target':[min(r['w'] for r in metrics['rows']),min(r['h'] for r in metrics['rows'])]})
                capture(device+'-fix-light'+('-origin' if theme=='origin' else '-contrast' if contrast else ''))
        passed('tablet phone Origin large text high contrast and reduced motion')
        # New text is available in all existing languages, without changing authored data.
        for language in ('en','es','fr','de'):
            a.js("document.querySelector('#langPick').value="+json.dumps(language)+";document.querySelector('#langPick').dispatchEvent(new Event('change'))")
            assert a.js("document.querySelector('#fixLights h3').textContent.trim().length>3")
            assert a.js("document.querySelectorAll('[data-light-group]').length")>0
        passed('Fix-a-light localization in all four languages')
        a.js("document.querySelector('#langPick').value='en';document.querySelector('#langPick').dispatchEvent(new Event('change'))")
        request(base,'/__test/no-key',{})
        a.wait(lambda:a.js("document.querySelector('#closePicker')!==null"))
        a.click('#closePicker')
        found=False
        for _ in range(24):
            found=a.js("!!document.querySelector('.no-key-note')")
            if found:break
            a.click('[data-region=right] .next')
        assert found,'No-key emote not reachable'
        assert a.js("document.querySelector('.no-key-note').textContent==='No key set'")
        before=events();a.click('.no-key')
        a.wait(lambda:len(events()['speech'])>len(before['speech']))
        after=events()
        assert after['invocations']==before['invocations']
        assert "no key set for the wave emote" in after['speech'][-1]
        capture('phone-no-key-emote')
        passed('no-key emote visibly labelled and tap speaks without command dispatch')
    if artifacts:
        (artifacts/'lights-results.json').write_text(json.dumps({'checks':results,'measurements':measurements},indent=2),encoding='utf-8')
    print(str(len(results))+' light browser checks passed; both Chrome processes stopped.')
