"""README pictures: synthetic data only, real production server and client.

Run: python tests/fake_server.py --showcase --artifacts <dir>
Unlike the acceptance runs, theme fonts load from Google Fonts, as on a tablet
with internet access, so the pictures match what players see. Captures are
taken at twice the CSS size and saved at 4/3 for sharp, moderate-size PNGs.
"""
import base64
import io
import json
import time

from run_e2e import Chrome, request

TARGET = "Hidden Cave Entrance"
# Camera looking just left of the target: a green bubble, slightly right and low.
CAMDIR = [0, -15, -75]
# A believable moment: landed, VTOL on, ship powered, weapons still cold.
LIT = ("gear", "vtol", "power", "flight-ready", "thruster-power", "shield-power", "lights")


def run(base, token, artifacts):
    from PIL import Image
    from showcase_ring import render as render_ring
    artifacts.mkdir(parents=True, exist_ok=True)

    def push(message):
        request(base, "/__test/push", message)

    def fresh():
        request(base, "/__test/nav", {"fresh": True, "camdir": CAMDIR})

    with Chrome() as b:
        b.call("Network.setBlockedURLs", {"urls": []})

        def settle(seconds=.5):
            b.js("new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))")
            time.sleep(seconds)

        def capture(name):
            settle()
            data = base64.b64decode(b.call("Page.captureScreenshot", {"format": "png"})["data"])
            image = Image.open(io.BytesIO(data)).convert("RGB")
            image = image.resize((round(image.width * 2 / 3), round(image.height * 2 / 3)), Image.LANCZOS)
            image.save(artifacts / (name + ".png"), optimize=True)
            print("captured", name, image.size, flush=True)

        def size(width, height, mobile=False):
            b.call("Emulation.setDeviceMetricsOverride",
                   {"width": width, "height": height, "deviceScaleFactor": 2, "mobile": mobile})
            settle()

        def open_deck(device):
            b.navigate(base + "/?t=" + token + "&device=" + device)
            b.wait(lambda: b.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"), 20)
            if b.js("!!document.querySelector('#intro') && !document.querySelector('#intro').classList.contains('gone')"):
                b.click("#intro")
                b.wait(lambda: b.js("document.querySelector('#intro').classList.contains('gone')"))
            b.wait(lambda: b.js("document.querySelectorAll('.hud-btn').length>5"))
            b.js("document.fonts.ready.then(()=>true)")
            settle(1)

        def title(region):
            return b.js("document.querySelector('[data-region=\"" + region + "\"] .hud-pager .title span').textContent")

        def page_to(region, wanted):
            for _ in range(24):
                if title(region) == wanted:
                    settle(.3)
                    return
                b.click('[data-region="' + region + '"] .next')
                time.sleep(.2)
            raise AssertionError((region, wanted))

        def choose(name):
            probe = b.js("(()=>{const e=[...document.querySelectorAll('.nd-waypoint')].find(r=>r.textContent.includes(" + json.dumps(name) + "));if(!e)return null;e.querySelector('.nd-choose').dataset.probe='pick';return true})()")
            assert probe, name
            b.click("[data-probe=pick]")
            b.wait(lambda: b.js("document.querySelector('.nd-target-name')?.textContent===" + json.dumps(name)))

        def guidance():
            fresh()
            b.wait(lambda: b.js("document.querySelector('.nd-bubble') && document.querySelector('.nd-bubble').style.display!=='none'"))
            settle(.8)

        size(1200, 900)
        open_deck("tablet")
        theme = b.js("document.documentElement.dataset.theme")
        home = title("right")
        # Lights come from the server's sync path, as after voice commands.
        for group in LIT:
            push({"t": "state", "group": group, "on": True})

        # Waypoints and navigation, using the desk's own pager and buttons.
        fresh()
        b.click("#nav-deck-link")
        b.wait(lambda: b.js("!!document.querySelector('[data-navdesk=nav]')"))
        page_to("right", "WAYPOINTS")
        b.wait(lambda: b.js("document.querySelectorAll('.nd-waypoint').length>=4"))
        choose(TARGET)
        guidance()
        capture("nav")
        page_to("right", "WAYPOINTS")
        capture("waypoints")

        # Origin, one of the twelve manufacturer themes, on the same desk.
        page_to("right", "NAV")
        b.js("document.documentElement.dataset.theme='origin'")
        guidance()
        capture("theme-origin")
        b.js("document.documentElement.dataset.theme=" + json.dumps(theme))

        # Deck settings: display options and Fix a light.
        b.js("document.querySelector('.js-settings, #themeBtn').click()")
        b.wait(lambda: b.js("document.querySelector('#picker').classList.contains('open') && document.querySelectorAll('[data-light-group]').length>0"))
        b.js("document.querySelector('#fixLights').scrollIntoView({block:'start'})")
        capture("settings-fix-a-light")
        b.click("#closePicker")

        # The cockpit as Wingman hears a voice command.
        page_to("right", home)
        fresh()
        settle(1)
        push({"t": "heard", "text": "Deploy the landing gear"})
        b.wait(lambda: b.js("document.querySelector('#toast').textContent.includes('landing gear')"))
        capture("cockpit")

        # Phone layout: same deck and lights, beside the shared NAV desk.
        size(852, 393, True)
        open_deck("phone")
        b.click("#nav-deck-link")
        b.wait(lambda: b.js("!!document.querySelector('[data-navdesk=nav]')"))
        guidance()
        capture("phone-nav")

        # Deck setup is opt-in: a control without its command says so and presses nothing.
        size(1200, 900)
        request(base, "/__test/not-set-up", {})
        open_deck("tablet")
        page_to("right", home)
        b.wait(lambda: b.js("!!document.querySelector('.not-set-up .no-key-note')"))
        b.click(".not-set-up")
        b.wait(lambda: b.js("document.querySelector('#toast').textContent.includes('Enable deck setup')"))
        capture("not-set-up")

    render_ring(artifacts / "ring.png")
    print("rendered ring", flush=True)
