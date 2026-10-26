"""First-run rendering and shared startup card text, with only synthetic data."""
import base64
from run_e2e import Chrome,request

def run(base,token,artifacts=None):
    with Chrome() as browser:
        request(base,'/__test/not-set-up',{})
        browser.navigate(base+'/?t='+token)
        browser.wait(lambda:browser.js("document.body.classList.contains('canvas-mode') && !document.body.classList.contains('offline')"))
        if browser.js("!document.querySelector('#intro').classList.contains('gone')"):
            browser.click('#intro');browser.wait(lambda:browser.js("document.querySelector('#intro').classList.contains('gone')"))
        browser.wait(lambda:browser.js("!!document.querySelector('.not-set-up .no-key-note')"))
        assert browser.js("document.querySelector('.not-set-up .no-key-note').textContent==='Not set up'")
        before=request(base,'/__test/events');browser.click('.not-set-up')
        browser.wait(lambda:browser.js("document.querySelector('#toast').textContent.includes('Enable deck setup')"))
        after=request(base,'/__test/events')
        assert after['invocations']==before['invocations'] and after['speech']==before['speech']
        if artifacts:
            (artifacts/'tablet-not-set-up.png').write_bytes(base64.b64decode(browser.call('Page.captureScreenshot',{'format':'png'})['data']))
        print('Not-set-up rendering and one inert visible notice PASS',flush=True)
        browser.navigate(base+'/__test/setup-card')
        browser.wait(lambda:browser.js("document.body.textContent.includes('Star Citizen must be closed')"))
        for text in ('Deck setup is off','Set up deck commands and keybinds','Backups are kept','does not remove'):
            assert browser.js('document.body.textContent.includes('+__import__('json').dumps(text)+')')
        if artifacts:
            (artifacts/'startup-card.png').write_bytes(base64.b64decode(browser.call('Page.captureScreenshot',{'format':'png'})['data']))
        print('Shared startup-card text in synthetic card preview PASS',flush=True)
