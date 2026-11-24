"""Local signed event sender for acceptance testing (no live Slack access)."""
import sys, json, os, hmac, hashlib, urllib.request
from pathlib import Path
if __name__=='__main__':
    if len(sys.argv)!=2:sys.exit('Usage: python send_event.py ../fixtures/event_01_success.json')
    raw=Path(sys.argv[1]).read_bytes()
    secret=os.getenv('EVENT_SIGNING_SECRET','local-signing-secret')
    sig=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    req=urllib.request.Request('http://127.0.0.1:8765/events/transaction',data=raw,method='POST',headers={'Content-Type':'application/json','X-Event-Signature':'sha256='+sig})
    try:
        with urllib.request.urlopen(req,timeout=5) as resp:print(resp.status,resp.read().decode())
    except Exception as e:print(type(e).__name__,str(e))
