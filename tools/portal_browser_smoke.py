"""Real-browser PORTAL help acceptance against intercepted fixtures, never account/feed IO."""
import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
ORIGIN='http://nexus-ui.invalid'
ORDER='pf_0123456789abcdef_7_BUY'


def fixtures(path,pending=True):
    if path=='/api/status':return dict(portal_enabled=False,halted=False)
    if path=='/api/tiers':return dict(tiers=[],tier_permissions=[dict(tier=t,enabled=False,maximum_intent_usd=n) for t,n in [('terrestrial',100),('solar',200),('universal',200)]])
    if path.startswith('/api/tiers/performance'):return dict(tiers=[])
    if path=='/api/markets':return dict(providers=[])
    if path=='/api/strategy-branches':return dict(branches=[])
    if path=='/api/paper/settings':return dict(paper_budget=10000,terrestrial_allocation=50,solar_allocation=35,universal_allocation=15)
    if path=='/api/paper/portfolio':return dict(initialized=False)
    if path=='/api/paper/ai-provider/status':return dict(configured=False,requests_used_process=0,max_requests_process=5)
    if path=='/api/observer/status':return dict(active=False,cycles=0,last_error=None)
    if path.startswith('/api/observer/history'):return dict(events=[])
    if path=='/api/paper/forward/recovery':
        return dict(provisional=False,runner_enabled=False,halted=False,
                    pending_orders=[dict(id=ORDER,side='BUY',status='SIMULATED_PARTIALLY_FILLED',simulated_filled_notional='49')] if pending else [],
                    positions={'BTC-USD':dict(quantity='0.49',entry='100')},
                    session_reviews=[dict(session='0123456789abcdef',reasons=['ORDER_CLEANUP_REQUIRES_REVIEW'])],sessions_examined=1)
    if path=='/api/execution/intent/'+ORDER:return dict(order=dict(id=ORDER,status='SIMULATED_PARTIALLY_FILLED'))
    if path=='/api/paper/forward':return dict(enabled=False,product='BTC-USD',cycles=7,tier='terrestrial',last_error=None,
            storage_capacity=dict(quote_observations_retained=7,quote_limit=100000,sessions_retained=1,session_limit=1000,warnings=[]),history=[])
    raise ValueError('UNEXPECTED_BROWSER_FIXTURE_REQUEST: '+path)


def validate_geometry(data):
    if data['position']!='fixed' or data['background']!='rgb(0, 0, 0)' or data['color']!='rgb(230, 164, 255)':
        raise ValueError('HELP_BROWSER_STYLE_DISAGREEMENT')
    if data['font_size']<16 or data['pointer_events']!='none':raise ValueError('HELP_BROWSER_LEGIBILITY_DISAGREEMENT')
    r=data['rect'];width=data['viewport_width'];height=data['viewport_height']
    if r['x']<0 or r['y']<0 or abs(width-r['right']-16)>1 or abs(height-r['bottom']-16)>1:
        raise ValueError('HELP_BROWSER_POSITION_DISAGREEMENT')
    if r['width']>width-30 or data['scroll_height']>data['client_height']+1:
        raise ValueError('HELP_BROWSER_TEXT_CLIPPED')


def run(output,channel=None):
    from playwright.sync_api import sync_playwright,expect
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    html=(ROOT/'dashboard/portal_preview.html').read_text(encoding='utf-8')
    reports=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,**({'channel':channel} if channel else {}))
        try:
            for width,height in ((1280,900),(390,844),(320,568)):
                context=browser.new_context(viewport=dict(width=width,height=height),service_workers='block')
                page=context.new_page();errors=[];blocked=[];writes=[];state={'pending':True};current_control=None
                page.add_init_script('''window.__helpEvents=[];for(const kind of ['pointerover','pointerout','scroll','focusin','focusout','blur'])document.addEventListener(kind,e=>{const c=e.target&&e.target.closest?e.target.closest('button,input,select,textarea'):null;window.__helpEvents.push({kind,id:c&&c.id,tag:c&&c.tagName,scrollY:window.scrollY});if(window.__helpEvents.length>60)window.__helpEvents.shift();},true);''')
                page.on('pageerror',lambda error:errors.append(str(error)))
                def route(request):
                    url=urlsplit(request.request.url);path=url.path+('?' + url.query if url.query else '')
                    if url.scheme+'://'+url.netloc!=ORIGIN:
                        blocked.append(request.request.url);request.abort();return
                    if path=='/portal':request.fulfill(status=200,content_type='text/html',body=html);return
                    if path=='/favicon.ico':request.fulfill(status=204);return
                    if request.request.method!='GET':
                        writes.append(path)
                        if path=='/api/execution/intent/'+ORDER+'/cancel':
                            state['pending']=False;request.fulfill(json=dict(status='SIMULATED_CANCELLED'));return
                        blocked.append('UNEXPECTED_WRITE '+path);request.abort();return
                    try:request.fulfill(json=fixtures(path,state['pending']))
                    except ValueError as exc:blocked.append(str(exc));request.abort()
                page.route('**/*',route)
                try:
                    page.goto(ORIGIN+'/portal')
                    expect(page.locator('#tier-engagement button')).to_have_count(3)
                    page.locator('#forward-recovery').click()
                    expect(page.get_by_role('button',name='Cancel Pending Remainder')).to_have_count(1)
                    panel=page.locator('#control-help');body=page.locator('#control-help-text')
                    tested=0
                    for control in page.locator('button,input,select,textarea').all():
                        if not control.is_visible():continue
                        current_control=dict(id=control.get_attribute('id'),tag=control.evaluate('e=>e.tagName'))
                        description=control.get_attribute('data-help')
                        if not description or len(description)<30:raise ValueError('HELP_BROWSER_CONTROL_MISSING_DESCRIPTION')
                        control.hover(force=True);expect(panel).to_be_visible();expect(body).to_have_text(description)
                        data=panel.evaluate('''e=>{const s=getComputedStyle(e),r=e.getBoundingClientRect();return {position:s.position,background:s.backgroundColor,color:s.color,font_size:parseFloat(s.fontSize),pointer_events:s.pointerEvents,rect:{x:r.x,y:r.y,right:r.right,bottom:r.bottom,width:r.width},viewport_width:innerWidth,viewport_height:innerHeight,scroll_height:e.scrollHeight,client_height:e.clientHeight}}''')
                        validate_geometry(data);tested+=1
                        page.mouse.move(0,0);expect(panel).to_be_hidden()
                    if tested<90:raise ValueError('HELP_BROWSER_CONTROL_COVERAGE_INCOMPLETE')
                    if writes:raise ValueError('HELP_BROWSER_HOVER_SENT_WRITE')
                    page.locator('#forward-tier').hover()
                    page.locator('#forward-tier').select_option('solar')
                    page.mouse.move(0,0);expect(panel).to_be_hidden()
                    page.locator('#paperbudget').focus();expect(panel).to_be_visible()
                    page.keyboard.press('Escape');expect(panel).to_be_hidden()
                    page.locator('#aikey').fill('PRIVATE-FIXTURE-NOT-A-REAL-KEY')
                    page.locator('#aikey').hover();expect(panel).to_be_visible()
                    if 'PRIVATE-FIXTURE' in body.inner_text():raise ValueError('HELP_BROWSER_INPUT_VALUE_EXPOSED')
                    page.locator('#aikey').fill('');page.mouse.move(0,0)
                    page.locator('#forward-start').hover();expect(panel).to_be_visible()
                    page.screenshot(path=str(output/f'help-{width}x{height}.png'))
                    if writes:raise ValueError('HELP_BROWSER_INTERACTION_SENT_WRITE')
                    if errors or blocked:raise ValueError('HELP_BROWSER_PAGE_ERRORS: '+repr(errors+blocked))
                    reports.append(dict(width=width,height=height,controls_hovered=tested,hover_sent_writes=False,keyboard_escape=True,private_value_isolated=True))
                except Exception as exc:
                    diagnostic=dict(viewport=dict(width=width,height=height),control=current_control,error=str(exc),page_errors=errors,blocked=blocked,
                                    events=page.evaluate('window.__helpEvents||[]'))
                    (output/f'failure-{width}x{height}.json').write_text(json.dumps(diagnostic,indent=2),encoding='utf-8')
                    page.screenshot(path=str(output/f'failure-{width}x{height}.png'))
                    raise
                finally:context.close()
        finally:browser.close()
    report=dict(mode='INTERCEPTED_FIXTURE_BROWSER_ACCEPTANCE',passed=True,viewports=reports,
                playwright_version=importlib.metadata.version('playwright'),
                html_sha256=hashlib.sha256(html.encode()).hexdigest(),source_commit=os.environ.get('GITHUB_SHA'),
                broker_connected=False,live_orders_enabled=False,
                limitations='Headless browser and intercepted fixtures only; no real-feed session, native desktop window, clean-machine profile or release approval.')
    (output/'browser-report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--channel',help='Optional installed Chromium channel, for example msedge')
    args=parser.parse_args();print(json.dumps(run(args.output,args.channel),indent=2))


if __name__=='__main__':main()
