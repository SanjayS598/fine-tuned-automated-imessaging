"""Content-preserving style transfer built solely from verified outgoing text."""
import hashlib
import json
import random
import re
from pathlib import Path
from . import config

STYLE_POLICY='owner_style_v2'
STYLE_SYSTEM='Rewrite the message in my casual texting style. Preserve its meaning, facts, numbers and intent. Output only the rewritten message.'
STYLE_PREFIX='Message to restyle:\n'


def neutralize(text):
    words={'u':'you','rn':'right now','idk':"I don't know",'tmrw':'tomorrow','tmr':'tomorrow',
           'bc':'because','bcuz':'because','ppl':'people','gonna':'going to','wanna':'want to',
           'kinda':'kind of','lmk':'let me know','thx':'thanks','pls':'please','plz':'please'}
    text=re.sub(r'\b(?:'+ '|'.join(words)+r')\b',lambda m:words[m[0].lower()],text,flags=re.I)
    text=re.sub(r'\bi\b','I',text)
    return text[:1].upper()+text[1:]


def style_entry(text,source):
    return dict(messages=[dict(role='system',content=STYLE_SYSTEM),
        dict(role='user',content=STYLE_PREFIX+neutralize(text)),dict(role='assistant',content=text)],
        source=dict(source,training_policy=STYLE_POLICY,text_sha256=hashlib.sha256(text.encode()).hexdigest()))


def validate_style(entry):
    messages=entry.get('messages',[]);source=entry.get('source',{})
    if len(messages)!=3 or [m.get('role') for m in messages]!=['system','user','assistant']:
        raise ValueError('Invalid owner style record.')
    text=messages[-1].get('content')
    if (source.get('is_from_me')!=1 or not source.get('message_guid') or source.get('training_policy')!=STYLE_POLICY
        or not isinstance(text,str) or not text.strip() or messages[0]['content']!=STYLE_SYSTEM
        or messages[1]['content']!=STYLE_PREFIX+neutralize(text)
        or source.get('text_sha256')!=hashlib.sha256(text.encode()).hexdigest()):
        raise ValueError('Style training must use only an outgoing message and its deterministic normalization.')


def export_style(output_dir,progress=None,limit=2048):
    from .training import export
    from .policy import validate_owner_entry
    raw=Path(output_dir).parent/'owner_source'
    stats=export(output_dir=raw,progress=progress)
    unique={}
    for split in ('train','valid'):
        with (raw/(split+'.jsonl')).open() as stream:
            for line in stream:
                entry=json.loads(line);validate_owner_entry(entry)
                text=entry['messages'][-1]['content'].strip()
                # Avoid training the unconditional acknowledgments that dominated v1.
                if not 4<=len(text.split())<=40 or not 20<=len(text)<=240:continue
                key=' '.join(neutralize(text).casefold().split())
                unique.setdefault(key,style_entry(text,entry['source']))
    buckets=[[],[],[]]
    for entry in unique.values():
        size=len(entry['messages'][-1]['content'].split())
        buckets[0 if size<=8 else 1 if size<=16 else 2].append(entry)
    rng=random.Random(73)
    for bucket in buckets:rng.shuffle(bucket)
    selected=[]
    while len(selected)<limit and any(buckets):
        for bucket in buckets:
            if bucket and len(selected)<limit:selected.append(bucket.pop())
    rng.shuffle(selected)
    if len(selected)<100:raise ValueError('Need at least 100 distinct substantive outgoing messages for style training.')
    cut=int(len(selected)*.9)
    output=Path(output_dir);config.private_dir(output)
    for split,entries in (('train',selected[:cut]),('valid',selected[cut:])):
        with (output/(split+'.jsonl')).open('w') as stream:
            for entry in entries:
                validate_style(entry);stream.write(json.dumps(entry,ensure_ascii=False)+'\n')
        stats[split+'_examples']=len(entries)
    stats.update(training_policy=STYLE_POLICY,training_recipe=STYLE_POLICY,curated_examples=len(selected),
        distinct_candidates=len(unique),incoming_messages_included=0,
        scope='Owner-only style transfer; normalized copies of your own messages as inputs; no incoming text')
    (output/'manifest.json').write_text(json.dumps(stats,indent=2))
    if progress:progress(**stats)
    return stats
