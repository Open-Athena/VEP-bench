import json, os, urllib.request, urllib.error
from pathlib import Path
p=Path(__file__).parent
os.umask(0o077)
base={'model':'google/gemini-4-argon','max_tokens':128,'reasoning':{'effort':'high','exclude':False},'messages':[{'role':'user','content':'Calculate 127 times 349 plus 563 times 811. Check your arithmetic and give the exact integer.'}],'provider':{'only':['google-vertex/global'],'allow_fallbacks':False,'require_parameters':True,'max_price':{'prompt':2,'completion':10}}}
body=base
for hop in (1,2):
    (p/f'request-{hop}.json').write_text(json.dumps(body))
    req=urllib.request.Request('https://openrouter.ai/api/v1/chat/completions',data=json.dumps(body).encode(),headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY'],'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=90) as r: raw=r.read(8*1024*1024)
    except urllib.error.HTTPError as e:
        (p/f'error-{hop}.json').write_bytes(e.read(1048576));print(json.dumps({'hop':hop,'http_error':e.code}),flush=True);break
    (p/f'response-{hop}.json').write_bytes(raw)
    r=json.loads(raw)
    if not r.get('choices'): print(json.dumps({'hop':hop,'error':True}));break
    c=r['choices'][0];m=c['message'];tokens=[d for d in m.get('reasoning_details',[]) if d.get('id')=='gemini-continuation' and d.get('data')]
    print(json.dumps({'hop':hop,'provider':r.get('provider'),'finish':c.get('finish_reason'),'native_finish':c.get('native_finish_reason'),'usage':r.get('usage'),'continuation_items':len(tokens)}),flush=True)
    if hop==2 or c.get('finish_reason')!='length' or not tokens:break
    body={**base,'max_tokens':256,'messages':base['messages']+[{'role':'assistant','content':'','reasoning_details':[tokens[-1]]}]}
