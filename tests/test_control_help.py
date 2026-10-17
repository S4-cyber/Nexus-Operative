from html.parser import HTMLParser
from pathlib import Path
import re
import shutil
import subprocess
import pytest

ROOT=Path(__file__).resolve().parents[1]
HTML=(ROOT/'dashboard/portal_preview.html').read_text(encoding='utf-8')


class Controls(HTMLParser):
    def __init__(self):super().__init__();self.controls=[];self.panel=None
    def handle_starttag(self,tag,attrs):
        data=dict(attrs)
        if tag in ('button','input','select','textarea') or data.get('role')=='button' or data.get('contenteditable')=='true':
            self.controls.append(data)
        if data.get('id')=='control-help':self.panel=data


def test_every_static_control_requires_practical_help():
    parsed=Controls();parsed.feed(HTML)
    assert len(parsed.controls)>=86
    assert all(len(c.get('data-help','').strip())>=30 for c in parsed.controls),[c.get('id') for c in parsed.controls if not c.get('data-help')]
    assert parsed.panel['role']=='tooltip' and 'hidden' in parsed.panel
    assert 'pointer-events:none' in HTML


def test_dynamic_buttons_must_use_the_help_factory():
    # All direct button creation belongs to the factory; future dynamic controls
    # must supply their own practical description rather than bypass help.
    assert len(re.findall(r"document\.createElement\(['\"]button['\"]\)",HTML))==1
    assert "button.dataset.help=description" in HTML
    assert len(re.findall(r'\bhelpButton\(',HTML))>=4


def test_help_colors_are_high_contrast_and_text_is_legible():
    css=HTML.split('#control-help{',1)[1].split('}',1)[0]
    assert 'position:fixed' in css and 'right:16px' in css and 'bottom:16px' in css
    assert 'max-width:calc(100vw - 32px)' in css
    assert 'background:#000' in css and 'color:#e6a4ff' in css and '16px/1.55' in css
    channels=[int('e6a4ff'[i:i+2],16)/255 for i in (0,2,4)]
    linear=[c/12.92 if c<=.04045 else ((c+.055)/1.055)**2.4 for c in channels]
    contrast=(sum(c*w for c,w in zip(linear,(.2126,.7152,.0722)))+.05)/.05
    assert contrast>=7


def test_actual_help_controller_behavior():
    node=shutil.which('node')
    if not node:pytest.skip('Node is required for the help-controller smoke check')
    result=subprocess.run([node,str(ROOT/'tools/control_help_smoke.cjs')],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
