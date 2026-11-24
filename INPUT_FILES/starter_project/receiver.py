"""Local Slack-compatible webhook receiver and inspectable delivery log."""
import json, os, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
LOG = Path(__file__).parent / 'logs' / 'received.jsonl'
class Receiver(BaseHTTPRequestHandler):
    def respond(self,code,data):
        b=json.dumps(data).encode();self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(b)));self.end_headers();self.wfile.write(b)
    def do_GET(self):
        if self.path=='/health':self.respond(200,{'ok':True,'service':'local-slack-receiver'})
        elif self.path=='/deliveries':
            entries=[json.loads(x) for x in LOG.read_text().splitlines()] if LOG.exists() else []
            self.respond(200,{'count':len(entries),'deliveries':entries})
        else:self.respond(404,{'error':'unknown route'})
    def do_POST(self):
        if self.path!='/slack/incoming':return self.respond(404,{'error':'unknown route'})
        try:
            size=int(self.headers.get('Content-Length','0'))
            if size<=0 or size>32768:return self.respond(413,{'error':'invalid body size'})
            data=json.loads(self.rfile.read(size));assert isinstance(data.get('text'),str) and data['text'].strip()
            assert isinstance(data.get('blocks'),list) and data['blocks']
        except (ValueError,AssertionError,KeyError,TypeError):return self.respond(400,{'error':'invalid Slack-format JSON'})
        LOG.parent.mkdir(exist_ok=True);entry={'received_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'payload':data}
        with LOG.open('a') as f:f.write(json.dumps(entry)+'\n')
        self.respond(202,{'accepted':True})
    def log_message(self,*args):pass
if __name__=='__main__':
    port=int(os.getenv('RECEIVER_PORT','8766'));print(f'Receiver listening on 127.0.0.1:{port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',port),Receiver).serve_forever()
