import json
import math
import re
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .messages import Messages, clean, conversation, decode_text
from .state import State


def examples(rows, system, include_leading=False):
    """Keep 6-hour sessions together; only the owner's replies are targets."""
    sessions, session, last_date = [], [], None
    for row in rows:
        date = row['date']
        if last_date is not None and date-last_date > 6*3600*1_000_000_000:
            if session:
                sessions.append(session)
            session = []
        session.append(row)
        last_date = date
    if session:
        sessions.append(session)
    groups = []
    for session in sessions:
        turns = conversation(session, max_chars=None, keep_leading=include_leading)
        batch = []
        for i, turn in enumerate(turns):
            if turn['role'] != 'assistant' or i == 0:
                continue
            start = max(0, i-7)
            while turns[start]['role'] != 'user':
                start += 1
            batch.append({'messages':[{'role':'system','content':system}]+turns[start:i+1]})
        if batch:
            groups.append(batch)
    return groups


def export(contacts=None, output_dir=None, progress=None):
    cfg = config.load()
    source, state = Messages(cfg['db_path']), State()
    groups, seen = [], set()
    chats = source.training_chats()
    orphans=source.orphan_history()
    if orphans:
        chats.append(dict(chat_id=0,service='unlinked',contact=None))
    stats = dict(chats_total=len(chats),chats_processed=0,messages_scanned=0,
                 usable_messages=0,outgoing_messages=0,excluded_messages=0,
                 duplicate_messages=0,generated_sessions_excluded=0,
                 services={},scope='All conversations: iMessage, SMS, RCS and groups')
    try:
        for index, chat in enumerate(chats):
            if contacts:
                try:
                    if config.normalize(chat['contact'] or '') not in contacts:
                        continue
                except ValueError:
                    continue
            rows = orphans if chat['chat_id']==0 else source.all_history(chat['chat_id'])
            filtered, session, last_date, generated = [], [], None, False
            for row in rows:
                guid = row['message_guid'] or f"row-{row['rowid']}"
                if guid in seen:
                    stats['duplicate_messages'] += 1
                    continue
                seen.add(guid)
                stats['messages_scanned'] += 1
                if not clean(row):
                    stats['excluded_messages'] += 1
                    continue
                text = decode_text(row)
                if not text:
                    stats['excluded_messages'] += 1
                    continue
                stats['usable_messages'] += 1
                stats['outgoing_messages'] += bool(row['is_from_me'])
                row['text'], row['attributedBody'] = text, None
                if chat['chat_id']==0:
                    # Unlinked rows have no reliable conversation context. Give
                    # each owner-written message its own neutral-context target.
                    groups.extend(examples([row],cfg['system_prompt'],include_leading=True))
                    continue
                if last_date is not None and row['date']-last_date > 6*3600*1_000_000_000:
                    if not generated:
                        filtered.extend(session)
                    else:
                        stats['generated_sessions_excluded'] += 1
                    session, generated = [], False
                session.append(row)
                last_date = row['date']
                if row['is_from_me'] and state.generated(chat['chat_id'],text):
                    generated = True
            if not generated:
                filtered.extend(session)
            else:
                stats['generated_sessions_excluded'] += 1
            groups.extend(examples(filtered,cfg['system_prompt'],include_leading=True))
            stats['chats_processed'] = index+1
            stats['services'][chat['service'] or 'unknown'] = stats['services'].get(chat['service'] or 'unknown',0)+1
            if progress:
                progress(stage='exporting',progress_percent=round(100*(index+1)/max(1,len(chats)),2),**stats)
    finally:
        state.close()
    if len(groups)<3:
        raise ValueError('Need at least 3 separate conversation sessions with your replies.')
    import random
    random.Random(42).shuffle(groups)
    cut = max(1,min(len(groups)-1,int(len(groups)*0.9)))
    output = Path(output_dir or config.HOME/'data')
    config.private_dir(output)
    for name,partition in (('train',groups[:cut]),('valid',groups[cut:])):
        count = 0
        with (output/f'{name}.jsonl').open('w') as stream:
            for group in partition:
                for item in group:
                    stream.write(json.dumps(item,ensure_ascii=False)+'\n')
                    count += 1
        stats[name+'_examples'] = count
    (output/'manifest.json').write_text(json.dumps(stats,indent=2))
    if progress:
        progress(**stats)
    print(f"Exported {stats['train_examples']} training and {stats['valid_examples']} validation examples.",flush=True)
    return stats


def fit_example(entry, tokenizer, limit=1024):
    messages = list(entry['messages'])
    while len(tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=False))>limit:
        if len(messages)<=3:
            return None
        del messages[1:3]
    return {'messages':messages}


def prepare_entry(entry,tokenizer,limit=1024):
    fitted = fit_example(entry,tokenizer,limit)
    if fitted:
        return [fitted]
    # Preserve long owner replies by splitting their target tokens. Keep the
    # recent incoming context, instead of dropping an otherwise usable message.
    system,user,target = entry['messages'][0],entry['messages'][-2],entry['messages'][-1]
    tokens = tokenizer.encode(target['content'],add_special_tokens=False)
    results = []
    for offset in range(0,len(tokens),limit//2):
        content = tokenizer.decode(tokens[offset:offset+limit//2])
        recent = tokenizer.decode(tokenizer.encode(user['content'],add_special_tokens=False)[-limit//4:])
        messages = [system,dict(role='user',content=recent),dict(role='assistant',content=content)]
        while len(tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=False))>limit:
            if not messages[1]['content']:
                raise ValueError('System prompt exceeds the training token budget. Shorten it before training.')
            messages[1]['content'] = messages[1]['content'][len(messages[1]['content'])//2+1:]
        results.append({'messages':messages})
    return results


TRAIN_REPORT = re.compile(r'Iter (\d+): Train loss ([\d.eE+-]+),.*?It/sec ([\d.eE+-]+),.*?Trained Tokens ([\d.eE+-]+), Peak mem ([\d.eE+-]+) GB')
VAL_REPORT = re.compile(r'Iter (\d+): Val loss ([\d.eE+-]+)')


def parse_report(line,total):
    match = TRAIN_REPORT.search(line)
    if match:
        step,loss,speed,tokens,memory = map(float,match.groups())
        return dict(stage='training',steps_completed=int(step),steps_total=total,
                    examples_seen=int(step),progress_percent=round(min(100,100*step/total),2),
                    train_loss=loss,steps_per_second=speed,trained_tokens=int(tokens),peak_memory_gb=memory,
                    eta_seconds=round((total-step)/speed) if speed>0 else None)
    match = VAL_REPORT.search(line)
    if match:
        return dict(stage='validating',validation_loss=float(match[2]))
    if 'Saved adapter weights to' in line:
        match = re.search(r'Iter (\d+)',line)
        return dict(last_checkpoint_step=int(match[1]) if match else 0)
    return {}


def train(iters=None,data_dir=None,adapter_dir=None,progress=None):
    cfg = config.load()
    if cfg['enabled']:
        raise ValueError('Turn replies off before training.')
    if iters is not None and iters<=0:
        raise ValueError('Iterations must be positive.')
    data = Path(data_dir or config.HOME/'data')
    if not all((data/(name+'.jsonl')).is_file() for name in ('train','valid')):
        raise ValueError('Run export first.')
    adapter = Path(adapter_dir or cfg['adapter_path'])
    if (adapter/'adapters.safetensors').exists():
        raise ValueError('Adapter already exists. Choose a new adapter path.')
    from transformers import AutoTokenizer
    if progress:
        progress(stage='preparing',progress_percent=0)
    tokenizer = AutoTokenizer.from_pretrained(cfg['model'])
    prepared = data.parent/'prepared_data'
    config.private_dir(prepared)
    counts,source_counts = {},{}
    for name in ('train','valid'):
        with (data/(name+'.jsonl')).open() as stream:
            total=sum(bool(line.strip()) for line in stream)
        count = 0
        with (data/(name+'.jsonl')).open() as stream,(prepared/(name+'.jsonl')).open('w') as output:
            for index,line in enumerate(stream):
                if not line.strip():
                    continue
                entries=prepare_entry(json.loads(line),tokenizer)
                for entry in entries:
                    output.write(json.dumps(entry,ensure_ascii=False)+'\n')
                    count += 1
                if progress and index%100==0:
                    progress(stage='preparing',preparing_split=name,prepared_examples=count,
                             progress_percent=round(100*(index+1)/max(1,total),2))
        if not count:
            raise ValueError(f'No {name} examples fit the token budget.')
        counts[name],source_counts[name]=count,total
        print(f'Prepared {count} {name} examples within 1024 tokens.',flush=True)
    # With batch size one, N steps visits every training example once in MLX's
    # shuffled epoch iterator. Validation examples are held out from gradients.
    steps = iters if iters is not None else counts['train']
    if progress:
        progress(stage='training',steps_completed=0,steps_total=steps,examples_seen=0,
                 progress_percent=0,prepared_train=counts['train'],prepared_valid=counts['valid'],
                 training_passes=steps/counts['train'],training_started=time.time())
    config.private_dir(adapter)
    command=[sys.executable,'-u','-m','mlx_lm','lora','--model',cfg['model'],
             '--train','--data',str(prepared),'--adapter-path',str(adapter),
             '--iters',str(steps),'--batch-size','1','--max-seq-length','1024',
             '--mask-prompt','--grad-checkpoint','--steps-per-report','10',
             '--steps-per-eval','1000','--save-every','500','--seed','42']
    if progress:
        with subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,bufsize=1) as child:
            for line in child.stdout:
                print(line,end='',flush=True)
                report=parse_report(line,steps)
                if report:
                    progress(**report)
            result=child.wait()
            if result:
                raise subprocess.CalledProcessError(result,command)
    else:
        subprocess.run(command,check=True)
    metadata=dict(model=cfg['model'],iterations=steps,train_examples=counts['train'],
                  validation_examples=counts['valid'],passes=steps/counts['train'],completed=time.time())
    (adapter/'personal-model.json').write_text(json.dumps(metadata))
    print('Training finished. Evaluate in preview mode before live replies.',flush=True)
    return metadata
