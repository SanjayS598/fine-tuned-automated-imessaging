"""Private, read-only local decision summaries. Never used for training or sending."""
import hashlib
import json
import re
import sys
import time

from . import config
from .context import context_prompt, CONTEXT_MESSAGES
from .messages import Messages, clean, decode_text

KINDS = {'agreed', 'commitment', 'proposed', 'open'}
SYSTEM = 'Extract plans from these messages. Return a JSON object like {"items":[{"kind":"proposed","text":"A short plan summary","evidence":[1]}]}. Use kind agreed only when both people accept, proposed for unaccepted suggestions, open for unresolved questions, commitment for a promise. Cite one or two message numbers for each item. Include proposals even if no final decision exists. Exclude unrelated banter.'




def snapshot(source, chat_id):
    rows=source.history(chat_id,limit=CONTEXT_MESSAGES)
    return [dict(id=r['rowid'],owner=bool(r['is_from_me']),text=decode_text(r),date=r['date'])
            for r in rows if clean(r) and decode_text(r)]


def fingerprint(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def cache_path(chat_id):
    return config.HOME/'summaries'/f'{int(chat_id)}.json'


def read(chat_id):
    try:return json.loads(cache_path(chat_id).read_text())
    except (OSError,ValueError):return {}


def save(chat_id,data):
    path=cache_path(chat_id)
    config.private_dir(path.parent)
    temp=path.with_suffix('.tmp')
    temp.write_text(json.dumps(data))
    temp.chmod(0o600)
    temp.replace(path)


def parse(text,rows,bot_ids=()):
    # Tolerate a fenced JSON response, but never render model-supplied HTML.
    text=text.strip()
    if text.startswith('```'):
        text=text.split('\n',1)[-1].rsplit('```',1)[0].strip()
    try:
        data,_=json.JSONDecoder().raw_decode(text)
    except ValueError:
        # Small local models sometimes omit quotes around a schema key.
        repaired=re.sub(r'([{,]\s*)(items|kind|text|evidence)\s*:',r'\1"\2":',text)
        data,_=json.JSONDecoder().raw_decode(repaired)
    if not isinstance(data,dict) or not isinstance(data.get('items'),list):
        raise ValueError('The local model returned an invalid summary. Try Refresh.')
    by_id={r['id']:r for r in rows}
    items=[]
    for item in data['items'][:8]:
        if not isinstance(item,dict) or item.get('kind') not in KINDS:continue
        title=item.get('text')
        evidence=item.get('evidence')
        if not isinstance(title,str) or not title.strip() or len(title)>600:continue
        if not isinstance(evidence,list) or not evidence or any(type(i) is not int or i not in by_id for i in evidence):continue
        if len(set(evidence))>3:continue
        evidence=list(dict.fromkeys(evidence))[:6]
        cited=[by_id[i] for i in evidence]
        source_text=' '.join(r['text'] for r in cited).lower()
        numbers=lambda value:set(re.findall(r'(?<!\w)\d+(?::\d+)?',value))
        if not numbers(title).issubset(numbers(source_text)):continue
        days={'today','tonight','tomorrow','yesterday','monday','tuesday','wednesday','thursday','friday','saturday','sunday'}
        words=lambda value:set(re.findall(r'[a-z]+',value.lower()))
        if not (words(title)&days).issubset(words(source_text)&days):continue
        kind=item['kind']
        if kind=='open' and not re.search(r'\?|\b(when|where|what|how|which|whether|earlier|later|decide|plan|schedule)\b',source_text):continue
        # A one-sided suggestion must never become a mutually agreed plan.
        if kind=='agreed' and {r['owner'] for r in cited}!={True,False}:kind='proposed'
        if kind=='commitment' and not any(r['owner'] for r in cited):kind='proposed'
        items.append(dict(kind=kind,text=title.strip(),evidence=[dict(r,bot=r['id'] in bot_ids) for r in cited],
                          bot_involved=any(r['id'] in bot_ids for r in cited)))
    if data['items'] and not items:
        raise ValueError('No summary items had valid source messages. Try Refresh.')
    return items


def summarize(model,tokenizer,rows,bot_ids=()):
    from mlx_lm import generate
    from mlx_lm.sample_utils import make_sampler
    transcript=[]
    # Short indices avoid long database IDs consuming the small model's output.
    indexed=[dict(row,id=i+1) for i,row in enumerate(rows)]
    indexed_bot={i+1 for i,row in enumerate(rows) if row['id'] in bot_ids}
    for row in indexed:
        speaker='Me' if row['owner'] else 'Friend'
        transcript.append(f"{row['id']}. {speaker}: {row['text']}")
    prompt,metrics=context_prompt(tokenizer,[dict(role='user',content='\n'.join(transcript))],SYSTEM)
    response=generate(model,tokenizer,prompt=prompt,max_tokens=700,sampler=make_sampler(temp=0),verbose=False)
    try:
        items=parse(response,indexed,indexed_bot)
        if not items and len(rows)>8:
            size=40 if len(rows)>40 else 20 if len(rows)>20 else 8
            return summarize(model,tokenizer,rows[-size:],bot_ids)
    except ValueError:
        # Retry a smaller recent window when the small model loses the format.
        if len(rows)>8:
            size=40 if len(rows)>40 else 20 if len(rows)>20 else 8
            return summarize(model,tokenizer,rows[-size:],bot_ids)
        raise
    for item in items:
        for evidence in item['evidence']:evidence['id']=rows[evidence['id']-1]['id']
    metrics['messages_used']=len(rows)
    return items,metrics


def run(chat_ids):
    from mlx_lm import load
    from .state import State
    cfg=config.load()
    source=Messages(cfg['db_path'])
    model=tokenizer=None
    state=State()
    try:
        for chat_id in chat_ids:
            rows=snapshot(source,chat_id)
            previous=read(chat_id)
            try:
                save(chat_id,dict(previous,stage='generating',error=None))
                if rows:
                    if model is None:model,tokenizer=load(cfg.get('summary_model',cfg['model']))
                    bot_ids={r['id'] for r in rows if r['owner'] and state.generated(chat_id,r['text'])}
                    batches=windows(rows)
                    items=[];seen=set();scanned=set();failed=0
                    for index,batch in enumerate(batches):
                        try:
                            notes,metrics=summarize(model,tokenizer,batch,bot_ids)
                            for item in notes:
                                key=(item['kind'],tuple(r['id'] for r in item['evidence']))
                                if key not in seen:
                                    seen.add(key);items.append(item)
                        except ValueError:
                            failed+=1;metrics={}
                        scanned.update(r['id'] for r in batch)
                        save(chat_id,dict(chat_id=chat_id,stage='generating',items=items[:12],updated=time.time(),
                            fingerprint=fingerprint(rows),messages_count=len(scanned),chunks_done=index+1,
                            chunks_total=len(batches),partial=bool(failed),error=None))
                    if failed==len(batches):raise ValueError('No supported summary could be generated.')
                    metrics['messages_used']=len(scanned)
                else:items,metrics=[],{}
                save(chat_id,dict(chat_id=chat_id,stage='ready',items=items[:12],updated=time.time(),
                                  fingerprint=fingerprint(rows),messages_count=metrics.get('messages_used',len(rows)),context=metrics,partial=bool(failed) if rows else False,error=None))
            except Exception as exc:
                save(chat_id,dict(previous,chat_id=chat_id,stage='failed',error='Local summary failed ('+type(exc).__name__+'). Try Refresh.'))
    finally:state.close()


def windows(rows):
    """Newest first, with overlap so a proposal and its answer stay together."""
    result=[]
    end=len(rows)
    while end>0:
        start=max(0,end-8)
        result.append(rows[start:end])
        if start==0:break
        end-=6
    return result


if __name__=='__main__':
    run([int(arg) for arg in sys.argv[1:]])
