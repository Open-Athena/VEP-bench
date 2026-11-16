"""Export a shareable snapshot; preserve original hashes and redact opaque state."""
import hashlib
import json
import re
from pathlib import Path

source = Path('.vepbench')
target = Path(__file__).parent / 'evidence'
manifest = []

def redact(value):
    if isinstance(value, list):
        return [redact(x) for x in value]
    if isinstance(value, dict):
        out = {k: redact(v) for k, v in value.items()}
        for k in ('continuationToken', 'continuation_token', 'encrypted_content'):
            if out.get(k):
                out[k] = '[REDACTED OPAQUE STATE]'
        if value.get('type') == 'reasoning.encrypted' and out.get('data'):
            out['data'] = '[REDACTED OPAQUE STATE]'
        return out
    return value

for path in sorted(source.rglob('*')):
    if not path.is_file() or '__pycache__' in path.parts:
        continue
    raw = path.read_bytes()
    text = raw.decode('utf-8')
    assert not re.search(r'sk-or-[A-Za-z0-9_-]{15,}', text), f'Credential pattern in {path}'
    output = raw
    try:
        if path.suffix == '.jsonl':
            values = [json.loads(line) for line in text.splitlines() if line.strip()]
            clean = redact(values)
            if clean != values:
                output = ('\n'.join(json.dumps(x, ensure_ascii=False) for x in clean)+'\n').encode()
        else:
            value = json.loads(text)
            clean = redact(value)
            if clean != value:
                output = (json.dumps(clean, indent=2, ensure_ascii=False)+'\n').encode()
    except json.JSONDecodeError:
        pass
    dest = target/path.relative_to(source)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(output)
    manifest.append({'path':str(path.relative_to(source)),
                     'original_sha256':hashlib.sha256(raw).hexdigest(),
                     'export_sha256':hashlib.sha256(output).hexdigest(),
                     'redacted':output != raw})
(target.parent/'export-manifest.json').write_text(json.dumps(manifest, indent=2)+'\n')
print(f'Exported {len(manifest)} files; redacted {sum(x["redacted"] for x in manifest)}')
