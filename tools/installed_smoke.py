"""Exercise a packaged application against an explicit disposable data directory."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.request


def request(base,path,method='GET'):
    with urllib.request.urlopen(urllib.request.Request(base+path,method=method),timeout=3) as response:
        raw=response.read().decode('utf-8')
        return raw if path=='/portal' else json.loads(raw)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--exe',required=True,type=Path)
    parser.add_argument('--data',required=True,type=Path)
    parser.add_argument('--version',required=True)
    parser.add_argument('--expect-halted',action='store_true')
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args()
    args.data.mkdir(parents=True,exist_ok=True)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'NEXUS_BROWSER_MODE':'1','NEXUS_PORT':str(port),
         'NEXUS_PORTABLE_DATA':str(args.data.resolve())}
    # Keep startup evidence in the artifact; avoid pipes that can fill and deadlock.
    with args.output.with_suffix('.log').open('w',encoding='utf-8') as log:
        proc=subprocess.Popen([str(args.exe.resolve())],env=env,stdout=log,stderr=subprocess.STDOUT)
        try:
            base=f'http://127.0.0.1:{port}';deadline=time.monotonic()+60
            while True:
                if proc.poll() is not None:raise RuntimeError('INSTALLED_EXE_EXITED_EARLY')
                try:
                    status=request(base,'/api/status');break
                except (OSError,urllib.error.URLError):
                    if time.monotonic()>deadline:raise RuntimeError('INSTALLED_START_TIMEOUT')
                    time.sleep(.25)
            assert status['version']==args.version, status
            assert status['halted'] is args.expect_halted, status
            assert not status['portal_enabled'] and not status['live_orders_enabled'] and not status['broker_connected']
            assert 'PORTAL' in request(base,'/portal')
            assert not request(base,'/api/paper/forward')['enabled']
            assert request(base,'/api/roadmap')['read_only']
            assert request(base,'/api/simulate/halt','POST')['halted']
            assert request(base,'/api/execution/recovery')['halted']
            args.output.write_text(json.dumps(dict(version=args.version,initial_halted=status['halted'],
                portal_served=True,forward_stopped=True,halt_persisted=True,
                scope='CI installed-process smoke; no native window, market feed or cross-version upgrade acceptance.'),indent=2),encoding='utf-8')
        finally:
            # PyInstaller onefile has a parent bootloader and application child.
            if os.name=='nt':
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],stdout=log,stderr=log,check=False)
            else:proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)


if __name__=='__main__':main()
