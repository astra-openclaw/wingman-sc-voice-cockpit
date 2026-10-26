"""1.1 desk acceptance: actual browser, production HTTP/WS, synthetic DB only."""
import base64
import json
import time
from run_e2e import Chrome, request


def run(base, token, artifacts=None):
    results = []
    geometry_results = []
    heading_results = []
    def passed(name):
        results.append(name)
        print(name + " PASS", flush=True)
    with Chrome() as b:
        def capture(name):
            if artifacts:
                artifacts.mkdir(parents=True, exist_ok=True)
                (artifacts/(name+".png")).write_bytes(base64.b64decode(b.call("Page.captureScreenshot", {"format":"png"})["data"]))
        def root():
            assert b.js("location.pathname === '/'"), "Desk left root"
        def click_text(text, scope=".navdesk"):
            selector = b.js("(()=>{const a=[...document.querySelectorAll("+json.dumps(scope+" button")+")].find(e=>e.textContent==="+json.dumps(text)+" && e.offsetHeight);if(!a)return null;a.dataset.probe='click';return '[data-probe=click]'})()")
            assert selector, (scope, text)
            b.click(selector); b.js("document.querySelector('[data-probe=click]')?.removeAttribute('data-probe')")
            root()
        def fresh():
            request(base, "/__test/nav", {"fresh":True})
            b.wait(lambda:b.js("!document.body.classList.contains('offline')"))
        def nav():
            if b.js("!!document.querySelector('#intro') && !document.querySelector('#intro').classList.contains('gone')"):
                b.click("#intro")
                b.wait(lambda:b.js("document.querySelector('#intro').classList.contains('gone')"))
            b.click("#nav-deck-link")
            b.wait(lambda:b.js("!!document.querySelector('[data-navdesk=nav]')"))
            root()
        def waypoints():
            # Navigate using the actual existing pager.
            for _ in range(24):
                if b.js("!!document.querySelector('[data-navdesk=waypoints]')"):break
                b.click('[data-region="right"] .next')
            assert b.js("!!document.querySelector('[data-navdesk=waypoints]')")
            root()
        def select(rowid):
            b.click('.nd-waypoint[data-id="'+str(rowid)+'"] .nd-choose')
            b.wait(lambda:b.js("!!document.querySelector('[data-navdesk=nav]')"))
            b.wait(lambda:b.js("document.querySelector('.nd-target-name').textContent==='Survey Alpha'"))
        def open_more(rowid):
            waypoints()
            b.click('.nd-waypoint[data-id="'+str(rowid)+'"] .nd-more')
            b.wait(lambda:b.js("!!document.querySelector('.nd-sheet')"))

        def settle():
            b.js("new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))")
        def headings():
            result=b.js("""(()=>{
              const ref=document.querySelector('#canvas .hud-header:not(.nd-heading)');
              const keys=['fontFamily','fontSize','fontWeight','lineHeight','letterSpacing','textTransform','color'];
              const style=e=>Object.fromEntries(keys.map(k=>[k,getComputedStyle(e)[k]]));
              return {theme:document.documentElement.dataset.theme,
                reference:style(ref),renderedHeight:ref.querySelector('span').getBoundingClientRect().height,accent:getComputedStyle(ref.querySelector('b')).color,
                headings:[...document.querySelectorAll('.nd-heading')].map(e=>({
                  style:style(e),renderedHeight:e.querySelector('span')?.getBoundingClientRect().height,accent:e.querySelector('b')&&getComputedStyle(e.querySelector('b')).color,
                  shared:e.classList.contains('hud-header'),position:getComputedStyle(e).position,
                  prefix:e.querySelector('b')?.textContent,label:e.querySelector('span')?.textContent}))};
            })()""")
            assert result["headings"]
            assert all(h["shared"] and h["position"]=="static" and h["prefix"]=="//"
                       and " " not in h["label"] and h["style"]==result["reference"]
                       and abs(h["renderedHeight"]-result["renderedHeight"])<1
                       and h["accent"]==result["accent"] for h in result["headings"]),result
            heading_results.append(result)
        def geometry_matrix(device, original_size):
            # Real viewport resizing exercises the production canvas fit path.
            dims=request(base,"/layout.json?t="+token+"&device="+device).get("canvas") if device!="grid" else None
            scales=(.5,.8,1.,1.3) if dims else (1.,)
            for scale in scales:
                width,height=(round(dims[0]*scale),round(dims[1]*scale)) if dims else original_size
                b.call("Emulation.setDeviceMetricsOverride",{"width":width,"height":height,"deviceScaleFactor":1,"mobile":False})
                settle()
                for theme in ('rsi','drake','aegis','anvil','origin','misc','crusader','argo','esperia','banu','aopoa','cnou'):
                    for large in (False,True):
                        b.js("document.documentElement.dataset.theme="+json.dumps(theme)+";document.documentElement.toggleAttribute('data-large',"+json.dumps(large)+")")
                        settle()
                        result=b.js("""(()=>{
                          const n=document.querySelector('#nav-deck-link'),r=n.getBoundingClientRect();
                          const canvas=document.body.classList.contains('canvas-mode')?document.querySelector('#canvas'):null;
                          const label=document.querySelector(canvas?'.hud-status .js-status':'#statusText');
                          const l=label.getBoundingClientRect(),cs=getComputedStyle(n),ls=getComputedStyle(label);
                          const intersect=(a,b)=>a.left<b.right&&a.right>b.left&&a.top<b.bottom&&a.bottom>b.top;
                          const controls=[...document.querySelectorAll(canvas?'#canvas > button':'header > button')].filter(e=>e.getBoundingClientRect().width);
                          const saved=n.querySelector('#nav-status').textContent;
                          n.querySelector('#nav-status').textContent='Synthetic long waypoint '.repeat(40);
                          const ellipsis=n.scrollWidth>n.clientWidth&&cs.textOverflow==='ellipsis'&&cs.overflow==='hidden';
                          n.querySelector('#nav-status').textContent=saved;
                          const text=document.createRange();text.selectNodeContents(n);
                          const t=text.getBoundingClientRect();
                          return {width:r.width,height:r.height,gap:r.top-l.bottom,textGap:t.top-l.bottom,
                            labelOverlap:intersect(r,l),buttonOverlap:controls.some(e=>intersect(r,e.getBoundingClientRect())),
                            caseMatch:cs.textTransform===ls.textTransform,trackingMatch:cs.letterSpacing===ls.letterSpacing,
                            ellipsis,scale:canvas?canvas.getBoundingClientRect().width/parseFloat(canvas.style.width):1};
                        })()""")
                        result.update(device=device,theme=theme,large=large,requested_scale=scale)
                        assert result["gap"]>=1.9 and result["textGap"]>=1.5 and not result["labelOverlap"],result
                        assert result["width"]>=44 and result["height"]>=44 and not result["buttonOverlap"],result
                        assert result["caseMatch"] and result["trackingMatch"] and result["ellipsis"],result
                        assert abs(result["scale"]-scale)<.005,result
                        geometry_results.append(result)
                print(device+" scale "+str(scale)+" geometry PASS",flush=True)
            b.js("document.documentElement.dataset.theme='rsi';document.documentElement.removeAttribute('data-large')")
            b.call("Emulation.setDeviceMetricsOverride",{"width":original_size[0],"height":original_size[1],"deviceScaleFactor":1,"mobile":device=='phone'})
            settle()

        b.navigate(base+"/?t="+token+"&device=tablet&probe=keep#nav")
        b.wait(lambda:b.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        if b.js("!document.querySelector('#intro').classList.contains('gone')"): b.click("#intro")
        b.wait(lambda:b.js("document.querySelector('#intro').classList.contains('gone')"))
        assert b.js("localStorage.getItem('token')") == token
        assert b.js("!new URLSearchParams(location.search).has('t') && new URLSearchParams(location.search).get('probe')==='keep' && new URLSearchParams(location.search).get('device')==='tablet' && location.hash==='#nav'")
        b.call("Page.reload")
        b.wait(lambda:b.js("document.readyState==='complete' && document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        assert b.js("!new URLSearchParams(location.search).has('t') && new URLSearchParams(location.search).get('probe')==='keep' && localStorage.getItem('token')") == token
        passed("saved pairing URL removes only t; first-load and reload stay paired")
        # A cookie-free document loaded without URL credentials uses saved t.
        b.call("Network.clearBrowserCookies"); b.navigate(base+"/")
        b.wait(lambda:b.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        nav(); passed("durable code link + status opens tablet NAV")
        assert b.js("![...document.querySelectorAll('a')].some(a=>a.getAttribute('href')==='/nav')")  # no page escape link
        waypoints()
        b.wait(lambda:b.js("document.querySelectorAll('.nd-waypoint').length===3"))
        points=request(base,"/api/navpoints")["navpoints"]
        alpha=next(p["id"] for p in points if p["name"]=="Survey Alpha")
        beta=next(p["id"] for p in points if p["name"]=="Survey Beta")
        b.js("document.querySelector('.nd-filter select').value='Hurston';document.querySelector('.nd-filter select').dispatchEvent(new Event('change'))")
        assert b.js("document.querySelectorAll('.nd-waypoint').length===1 && document.querySelector('.nd-choose').textContent.includes('Far Survey')")
        b.js("document.querySelector('.nd-filter select').value='';document.querySelector('.nd-filter select').dispatchEvent(new Event('change'))")
        passed("API waypoint list + body filter")
        fresh(); select(alpha)
        b.wait(lambda:b.js("document.querySelector('.nd-distance').textContent==='1.0 km'"))
        b.wait(lambda:b.js("document.querySelector('.nd-bubble').style.display!=='none'"))
        headings()
        geometry_matrix("tablet",(1200,900))
        fresh()
        capture("tablet-nav")
        if artifacts:
            clip={"x":88,"y":20,"width":600,"height":75,"scale":4}
            (artifacts/"tablet-header-4x.png").write_bytes(base64.b64decode(b.call("Page.captureScreenshot",{"format":"png","clip":clip})["data"]))
        passed("tablet 96 theme/size/scale header geometries + micro-label ellipsis")
        passed("waypoint tap sets target + real NAV guidance")
        waypoints()
        assert b.js("document.querySelector('.nd-waypoint.nd-active').dataset.id") == str(alpha)
        start=time.monotonic()
        request(base,"/__test/nav",{"external":True})
        b.wait(lambda:b.js("document.querySelectorAll('.nd-waypoint').length===4"),2)
        assert time.monotonic()-start < 2
        passed("external DB revision refresh under 2s")
        open_more(alpha); headings(); capture("tablet-waypoints-sheet")
        b.js("document.querySelector('.nd-dialog input').value='Survey Alpha Renamed'")
        click_text("Rename",".nd-dialog")
        b.wait(lambda:any(p["name"]=="Survey Alpha Renamed" for p in request(base,"/api/navpoints")["navpoints"]))
        b.wait(lambda:b.js("document.querySelector('.nd-sheet-status').textContent==='Saved' && !document.querySelector('.nd-dialog button').disabled"))
        b.js("document.querySelector('.nd-dialog textarea').value='Synthetic updated notes'")
        click_text("Save Notes",".nd-dialog")
        b.wait(lambda:any(p.get("notes")=="Synthetic updated notes" for p in request(base,"/api/navpoints")["navpoints"]))
        b.wait(lambda:b.js("!document.querySelector('.nd-dialog button').disabled"))
        click_text("Close",".nd-dialog"); nav()
        b.wait(lambda:b.js("document.querySelector('.nd-target-name').textContent==='Survey Alpha Renamed'"))
        passed("sheet rename + notes persist + active target refresh")
        open_more(beta); click_text("Delete",".nd-dialog")
        assert any(p["id"]==beta for p in request(base,"/api/navpoints")["navpoints"]), "First delete click removed data"
        click_text("Cancel",".nd-dialog")
        assert any(p["id"]==beta for p in request(base,"/api/navpoints")["navpoints"])
        click_text("Delete",".nd-dialog"); click_text("Confirm Delete",".nd-dialog")
        b.wait(lambda:not any(p["id"]==beta for p in request(base,"/api/navpoints")["navpoints"]))
        b.wait(lambda:b.js("!document.querySelector('.nd-sheet')"))
        passed("delete requires explicit confirmation; cancel preserves")
        nav(); click_text("Clear Target")
        b.wait(lambda:b.js("document.querySelector('.nd-target-name').textContent==='No target set'"))
        before=len(request(base,"/api/navpoints")["navpoints"])
        click_text("Mark Location"); waypoints()
        b.wait(lambda:len(request(base,"/api/navpoints")["navpoints"])==before+1)
        b.wait(lambda:b.js("document.querySelectorAll('.nd-waypoint').length")==before+1,2)
        nav(); click_text("Reset Lights")
        events=request(base,"/__test/events")["events"]
        assert any(e.get("id")=="nav-clear" for e in events)
        assert any(e.get("id")=="nav-mark" for e in events)
        assert any(e.get("t")=="reset-states" for e in events)
        passed("Mark / Clear in-process path + Reset Lights")
        # Select renamed target, then stale data must blank the direction/ring.
        waypoints(); b.click('.nd-waypoint[data-id="'+str(alpha)+'"] .nd-choose')
        b.wait(lambda:b.js("!!document.querySelector('[data-navdesk=nav]')"))
        fresh()
        b.wait(lambda:b.js("document.querySelector('.nd-bubble').style.display!=='none'"))
        request(base,"/__test/nav",{"stale":True})
        b.wait(lambda:b.js("document.querySelector('.nd-bubble').style.display==='none' && document.querySelector('.nd-position').textContent.includes('STALE')"))
        assert b.js("document.querySelector('.nd-distance').textContent") == "Unavailable"
        assert "s" in b.js("document.querySelector('.nd-age').textContent")
        fresh(); b.wait(lambda:b.js("document.querySelector('.nd-bubble').style.display!=='none'"))
        b.call("Emulation.setEmulatedMedia",{"features":[{"name":"prefers-reduced-motion","value":"reduce"}]})
        assert b.js("getComputedStyle(document.querySelector('.nd-bubble')).animationName==='none' && getComputedStyle(document.querySelector('.nd-bubble')).transitionDuration==='0s'")
        passed("stale honesty + age + reduced motion")

        # Every theme inherits legible token contrast; no custom fixed colours.
        theme_results = b.js("""(()=>{
          function lum(rgb){return rgb.match(/[\\d.]+/g).slice(0,3).map(Number).map(n=>{n/=255;return n<=.04045?n/12.92:((n+.055)/1.055)**2.4}).reduce((a,n,i)=>a+n*[.2126,.7152,.0722][i],0);}
          return ['rsi','drake','aegis','anvil','origin','misc','crusader','argo','esperia','banu','aopoa','cnou'].map(t=>{
            document.documentElement.dataset.theme=t;
            const el=document.querySelector('.navdesk'),cs=getComputedStyle(el), l=lum(cs.color),bg=lum(cs.backgroundColor);
            const dim=lum(getComputedStyle(document.querySelector('.nd-meta')).color);
            return {theme:t,contrast:(Math.max(l,bg)+.05)/(Math.min(l,bg)+.05),dimContrast:(Math.max(dim,bg)+.05)/(Math.min(dim,bg)+.05)};
          });
        })()""")
        assert all(r["contrast"]>=4.5 and r["dimContrast"]>=3 for r in theme_results),theme_results
        b.js("document.documentElement.dataset.theme='origin'")
        settle(); headings(); capture("origin-nav")
        open_more(alpha); headings(); click_text("Close",".nd-dialog"); nav()
        passed("desk and sheet headings match deck hud-header typography and accent")
        b.js("document.documentElement.toggleAttribute('data-night',true);document.documentElement.toggleAttribute('data-large',true);document.documentElement.toggleAttribute('data-contrast',true);document.documentElement.toggleAttribute('data-no-lights',true)")
        assert b.js("getComputedStyle(document.querySelector('.navdesk')).fontSize==='17px'")
        open_more(alpha)
        assert b.js("Number(getComputedStyle(document.querySelector('.nd-sheet')).zIndex) > Number(getComputedStyle(document.querySelector('#night')).zIndex)")
        click_text("Close",".nd-dialog")
        passed("12 theme contrast + Origin + night/large/high-contrast/no-lights")

        b.call("Emulation.setDeviceMetricsOverride",{"width":852,"height":393,"deviceScaleFactor":1,"mobile":True})
        b.navigate(base+"/?device=phone")
        b.wait(lambda:b.js("document.documentElement.dataset.device==='phone' && document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        nav(); fresh()
        geometry_matrix("phone",(852,393))
        fresh(); capture("phone-nav")
        passed("phone 96 theme/size/scale header geometries + micro-label ellipsis")
        scrolled=b.js("(()=>{const e=document.querySelector('.nd-guidance');e.scrollTop=e.scrollHeight;return e.scrollTop})()")
        assert scrolled>0
        # The next real push must not jump away from the position/route being read.
        b.wait(lambda:b.js("document.querySelector('.nd-age').textContent.includes('1.')"),3)
        assert abs(b.js("document.querySelector('.nd-guidance').scrollTop")-scrolled)<2
        b.js("document.querySelector('.nd-guidance').scrollTop=0")
        def bounds(scope):
            return b.js("""(()=>{const scope=document.querySelector(%s);return [...scope.querySelectorAll('button,select,input,textarea')].filter(e=>e.offsetHeight).map(e=>{e.scrollIntoView({block:'nearest'});const r=e.getBoundingClientRect(),p=scope.getBoundingClientRect();return {text:e.textContent.slice(0,25),w:r.width,h:r.height,inside:r.x>=p.x-1&&r.y>=p.y-1&&r.right<=p.right+1&&r.bottom<=p.bottom+1}})})()""" % json.dumps(scope))
        for mode in ("nav","waypoints"):
            if mode=="waypoints":waypoints()
            measured=bounds(".navdesk")
            assert measured and all(r["w"]>=43.99 and r["h"]>=43.99 and r["inside"] for r in measured),measured
        open_more(alpha)
        measured=bounds(".nd-dialog")
        assert measured and all(r["w"]>=43.99 and r["h"]>=43.99 and r["inside"] for r in measured),measured
        click_text("Close",".nd-dialog"); nav()
        status=b.js("(()=>{let r=document.querySelector('#nav-deck-link').getBoundingClientRect();return [r.width,r.height]})()")
        assert min(status)>=43.99,status
        passed("phone NAV / WAYPOINTS / sheet controls >=44px inside panels")
        b.js("document.documentElement.toggleAttribute('data-large',true);document.documentElement.toggleAttribute('data-contrast',true);document.documentElement.toggleAttribute('data-night',true);document.documentElement.toggleAttribute('data-no-lights',true)")
        measured=bounds(".navdesk")
        assert all(r["w"]>=43.99 and r["h"]>=43.99 and r["inside"] for r in measured),measured
        passed("phone large-text high-contrast night controls")

        # Switch with the existing layout selector, then use the same shared desk.
        b.click('#canvas [aria-label="Deck settings"]')
        b.wait(lambda:b.js("document.querySelector('#picker').classList.contains('open')"))
        b.wait(lambda:b.js("[...document.querySelector('#layoutPick').options].some(o=>o.value==='star-citizen.json')"))
        b.js("document.querySelector('#layoutPick').value='star-citizen.json';document.querySelector('#layoutPick').dispatchEvent(new Event('change'))")
        b.wait(lambda:b.js("!document.body.classList.contains('canvas-mode')"))
        b.call("Emulation.setDeviceMetricsOverride",{"width":1200,"height":900,"deviceScaleFactor":1,"mobile":False})
        b.click("#nav-deck-link")
        b.wait(lambda:b.js("!!document.querySelector('[data-navdesk=both]')"))
        assert b.js("!!document.querySelector('.nd-target-name')")
        click_text("WAYPOINTS")
        b.wait(lambda:b.js("document.querySelector('.nd-waypoints').offsetHeight>0"))
        assert b.js("document.querySelectorAll('.nd-waypoint').length") == before+1
        click_text("NAV")
        root();passed("grid NAV page uses same desk; never leaves /")
        geometry_matrix("grid",(1200,900))
        passed("grid 24 theme/size header geometries + micro-label ellipsis")
        # No missing translations for desk UI strings; user waypoint data unchanged.
        b.js("document.querySelector('#langPick').value='de';document.querySelector('#langPick').dispatchEvent(new Event('change'))")
        b.wait(lambda:b.js("document.querySelector('.nd-target-name')?.textContent==='Survey Alpha Renamed'"))
        assert b.js("document.querySelector('.nd-distance').previousElementSibling.textContent")=="Entfernung"
        root();passed("language redraw preserves user data and translates desk")
    if artifacts:
        (artifacts/"navdesk-results.json").write_text(json.dumps({"checks":results,"themes":theme_results,"header_geometries":geometry_results,"heading_styles":heading_results,"chrome_stopped":True},indent=2),encoding="utf-8")
    print(f"{len(results)} NAV desk browser checks passed; Chrome stopped.",flush=True)
    return results
