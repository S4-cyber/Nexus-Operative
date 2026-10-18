"""Loopback-only HTTP boundary. No reverse proxy/cloud authentication fallback."""
import re
from starlette.responses import JSONResponse


class LocalHTTPBoundary:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']=='websocket':
            await send({'type':'websocket.close','code':1008});return
        if scope['type']!='http':
            await self.app(scope,receive,send);return
        headers=scope.get('headers',[])
        def values(name):return [value for key,value in headers if key.lower()==name]
        hosts,origins,sites=values(b'host'),values(b'origin'),values(b'sec-fetch-site')
        allowed=False
        if len(hosts)==1 and len(origins)<=1 and len(sites)<=1:
            try:
                host=hosts[0].decode('ascii');scheme=scope.get('scheme')
                matched=re.fullmatch(r'(localhost|127\.0\.0\.1)(?::([0-9]{1,5}))?',host)
                peer=scope.get('client')
                allowed=bool(matched and (matched[2] is None or 1<=int(matched[2])<=65535) and
                    scheme in ('http','https') and peer and peer[0] in ('127.0.0.1','::1') and
                    (not origins or origins[0].decode('ascii')==scheme+'://'+host) and
                    (not sites or sites[0] in (b'same-origin',b'same-site',b'none')))
            except (ValueError,UnicodeError):allowed=False
        if not allowed:
            path=scope.get('path','')
            detail='LOCAL_HTTP_REQUIRED'
            if path.startswith('/api/execution/alpaca-paper/local-credentials/'):detail='LOCAL_PAPER_PORTAL_REQUIRED'
            elif path.startswith('/api/paper/ai-provider/vault/'):detail='LOCAL_AI_PORTAL_REQUIRED'
            await JSONResponse({'detail':detail},403,headers={'Cache-Control':'no-store','Pragma':'no-cache'})(scope,receive,send)
            return
        async def protected(message):
            if message['type']=='http.response.start':
                pairs=list(message.get('headers',[]))
                additions={b'x-content-type-options':b'nosniff',b'x-frame-options':b'DENY',b'referrer-policy':b'no-referrer'}
                if scope.get('path','').startswith('/api/'):
                    additions.update({b'cache-control':b'no-store',b'pragma':b'no-cache'})
                pairs=[(key,value) for key,value in pairs if key.lower() not in additions]
                message=dict(message,headers=pairs+list(additions.items()))
            await send(message)
        await self.app(scope,receive,protected)
