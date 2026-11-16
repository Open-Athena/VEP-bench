import hashlib
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean

p = Path(__file__).parent

def summarize(path, effort):
    groups = defaultdict(list)
    ids = set()
    expected_digest = None
    for line in path.open():
        r = json.loads(line)
        assert r['question_id'] not in ids
        ids.add(r['question_id'])
        if expected_digest is None:
            expected_digest = r['question_set_sha256']
        assert r['question_set_sha256'] == expected_digest
        assert r['generation_parameters'] == {'max_tokens':128000,'reasoning':{'effort':effort,'exclude':False}}
        groups[r['question_id'].split(':')[0]].append(r)
    out = {}
    for task, rows in sorted(groups.items()):
        valid = [r for r in rows if r['response']['status'] == 'completed' and r['scoring']['valid']]
        errors = [r for r in rows if r['response']['status'] == 'api_error']
        out[task] = {
            'questions': len(rows),
            'completed': len(rows)-len(errors),
            'valid': len(valid),
            'api_errors': len(errors),
            'truncated': sum(r['response']['finish_reason']=='length' for r in rows),
            'invalid_completed': len(rows)-len(errors)-len(valid),
            'mean_spearman_rho': mean(r['scoring']['spearman_rho'] for r in valid) if len(valid)==len(rows) else None,
            'mean_pearson_r': mean(r['scoring']['pearson_r'] for r in valid) if len(valid)==len(rows) else None,
            'cost_usd': sum((r['usage'] or {}).get('cost',0) or 0 for r in rows if r not in errors),
            'completion_tokens': sum((r['usage'] or {}).get('completion_tokens',0) or 0 for r in rows if r not in errors),
        }
    return {'questions':len(ids),'question_set_sha256':expected_digest,'tasks':out,
            'cost_usd':sum(t['cost_usd'] for t in out.values()),
            'api_errors':sum(t['api_errors'] for t in out.values()),
            'valid':sum(t['valid'] for t in out.values()),
            'truncated':sum(t['truncated'] for t in out.values())}

current = summarize(p/'results.jsonl', 'medium')
assert current['questions']==52
previous = summarize(Path('.vepbench/campaigns/gemini-4-argon-low-all-20261030/results.jsonl'), 'low')
assert current['question_set_sha256'] == previous['question_set_sha256']
report = {'current':current,'low_20261030':previous,
          'cost_source':'provider batch total allocated across questions'}
(p/'summary.json').write_text(json.dumps(report,indent=2)+'\n')
files=[p/'batch-state.json',p/'results.jsonl',p/'summary.json',p/'preflight.json']
(p/'sha256.json').write_text(json.dumps({f.name:hashlib.sha256(f.read_bytes()).hexdigest() for f in files},indent=2)+'\n')
print(json.dumps(report,indent=2))
