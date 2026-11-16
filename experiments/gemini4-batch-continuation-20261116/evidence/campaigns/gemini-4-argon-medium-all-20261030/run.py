import json, os, sys, urllib.request
from pathlib import Path
from dataclasses import asdict
from vepbench.evaluation.batch import submit_batch_file, refresh_batch_state, collect_batch_file
from vepbench.resources import QUESTION_SCHEMA, RESULT_SCHEMA
p=Path(__file__).parent
q=Path('.vepbench/questions/main-be62fd7b5f06.jsonl')
key=os.environ['OPENROUTER_API_KEY']
if sys.argv[1]=='submit':
 assert not (p/'batch-state.json').exists()
 questions=[json.loads(line) for line in q.open()]
 assert len(questions)==52
 assert all(x['task_type']=='ranking' for x in questions)
 with urllib.request.urlopen('https://openrouter.ai/api/v1/models/google/gemini-4-argon:batch/endpoints',timeout=30) as r: metadata=json.load(r)
 endpoints=metadata['data']['endpoints']; assert endpoints
 assert all(float(e['pricing']['prompt'])<=0.000001 and float(e['pricing']['completion'])<=0.000005 for e in endpoints)
 (p/'preflight.json').write_text(json.dumps(metadata,indent=2)+'\n')
 s=submit_batch_file(questions_path=q,question_schema_path=QUESTION_SCHEMA,state_path=p/'batch-state.json',result_output=p/'results.jsonl',run_id='gemini-4-argon-medium-all-20261030',model_id='google/gemini-4-argon',api_key=key,generation_parameters={'max_tokens':128000,'reasoning':{'effort':'medium','exclude':False}})
else:
 s=refresh_batch_state(state_path=p/'batch-state.json',api_key=key)
 if s.status=='completed' and not (p/'results.jsonl').exists():
  print(json.dumps(asdict(s),default=str))
  s=collect_batch_file(state_path=p/'batch-state.json',questions_path=q,question_schema_path=QUESTION_SCHEMA,result_schema_path=RESULT_SCHEMA,api_key=key)
print(json.dumps(asdict(s),default=str,indent=2))
