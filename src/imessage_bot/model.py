import re
from pathlib import Path
from .policy import owner_adapter
from .context import context_prompt,inference_system,REPLY_CONTEXT_TOKENS
from .style_training import STYLE_POLICY,STYLE_SYSTEM,STYLE_PREFIX
from .texting import finish,guidance


def reply_config(cfg,chat_id,group=False):
    result=dict(cfg)
    instructions=cfg.get('chat_settings',{}).get(str(chat_id),{}).get('instructions','').strip()
    result['_chat_instructions']=instructions
    result['_is_group']=group
    if instructions:
        result['system_prompt']+="\n\nOwner's instructions for this conversation:\n"+instructions
    if group:
        result['system_prompt']+='\n\nThis is a group conversation. Incoming texts identify participants. Write one short reply to the group as the owner. Do not impersonate other participants or continue their dialogue.'
    return result


def sends_automatically(cfg,chat_id):
    mode=cfg.get('chat_settings',{}).get(str(chat_id),{}).get('reply_mode')
    return mode=='auto' if mode is not None else not cfg['dry_run']


class Model:
    def __init__(self, cfg):
        if not owner_adapter(cfg):
            raise ValueError('An owner-only model is required. Earlier mixed-context adapters are disabled; train a new version.')
        adapter = Path(cfg["adapter_path"])
        if not (adapter / "adapters.safetensors").is_file():
            raise ValueError("No trained adapter. Run export and train before generating replies.")
        import json
        metadata = json.loads((adapter / "personal-model.json").read_text())
        if metadata["model"] != cfg["model"]:
            raise ValueError("Adapter and base model do not match. Train again or restore the model setting.")
        from mlx_lm import load
        self.model, self.tokenizer = load(cfg["model"], adapter_path=str(adapter))
        self.style_policy=metadata['training_policy']
        self.base_model = None
        self.base_tokenizer = None
        self.last_context = {}

    @staticmethod
    def clean(text):
        if "<think>" in text and "</think>" not in text:
            return ''
        text=re.sub(r'<think>.*?</think>','',text,flags=re.S).strip()
        text=re.sub(r'^\[(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) \d{1,2} \d{2}:\d{2}\]\s*','',text).strip()
        if not text or len(text)>1000 or any(x in text for x in ('<|','[INST]','</think>')):
            return ''
        return text

    def reply(self, turns, cfg):
        from mlx_lm import generate,load
        from mlx_lm.sample_utils import make_sampler
        def progress(stage):
            callback=cfg.get('_progress')
            if callback:callback(stage)
        def complete(model,tokenizer,prompt,max_tokens=160,temp=.2):
            return self.clean(generate(model,tokenizer,prompt=prompt,max_tokens=max_tokens,
                                       sampler=make_sampler(temp=temp),verbose=False))
        progress('reading_context')
        if self.base_model is None:
            progress('loading_context_model')
            self.base_model,self.base_tokenizer=load(cfg.get('reply_model',cfg['model']))
        system=inference_system(cfg)
        if re.search(r'do you think|should we|let.s do earlier|how about',turns[-1]['content'],re.I):
            system+=' They are asking for a suggestion. Give one tentative option directly, following the owner instructions, instead of deflecting with questions about other people.'
        if guidance(cfg) and re.search(r'\b(?:your|ur) bot\b',turns[-1]['content'],re.I):
            system+=' They are referring to the owner’s bot as software separate from the owner. React as its owner to that comment, without scripted thanks or an unrelated plan.'
        prompt,metrics=context_prompt(self.base_tokenizer,turns,system,
                                      cfg.get('context_tokens',REPLY_CONTEXT_TOKENS))
        metrics['context_messages_available']=cfg.get('_context_message_count',len(turns))
        metrics['style_fallback']=False
        self.last_context=metrics
        progress('generating_reply')
        grounded=trim_invented_period(complete(self.base_model,self.base_tokenizer,prompt,temp=0),turns)
        if not grounded:
            return ''
        def supported(draft):
            progress('checking_context')
            return not unsupported_fact_numbers(draft,turns)
        metrics['context_repair']=False
        if not supported(grounded):
            metrics['context_repair']=True
            repair,_=context_prompt(self.base_tokenizer,turns,inference_system(cfg)+' Do not invent missing facts or promise future reminders. If the requested detail is not in these messages, ask one short clarifying question.',cfg.get('context_tokens',REPLY_CONTEXT_TOKENS))
            grounded=trim_invented_period(complete(self.base_model,self.base_tokenizer,repair,temp=0),turns)
            if not grounded or not supported(grounded):
                latest=turns[-1]['content'].lower()
                grounded='What time did we say?' if 'time' in latest or 'when' in latest else 'Where were we meeting?' if 'where' in latest or 'address' in latest else 'What do you mean?'
        if self.style_policy!=STYLE_POLICY:
            metrics['style_fallback']=True
            return finish(grounded,cfg)
        progress('personalizing_reply')
        style_prompt=self.tokenizer.apply_chat_template([
            dict(role='system',content=STYLE_SYSTEM),
            dict(role='user',content=STYLE_PREFIX+grounded)
        ],tokenize=False,add_generation_prompt=True,return_dict=False)
        styled=complete(self.model,self.tokenizer,style_prompt,temp=0)
        padded=len(styled.split())>max(len(grounded.split())+2,int(len(grounded.split())*1.4))
        if not styled or padded or not preserves_key_facts(grounded,styled) or generic_ack(styled) and not generic_ack(grounded):
            metrics['style_fallback']=True
            return finish(grounded,cfg)
        if styled==grounded:
            return finish(grounded,cfg)
        progress('checking_reply')
        check=self.base_tokenizer.apply_chat_template([
            dict(role='system',content='Compare a draft and a style rewrite. Output exactly KEEP only if the rewrite preserves all facts, intent, answers and commitments, without adding details or dropping the answer. Otherwise output REJECT. Short wording is fine; changing the answer is not.'),
            dict(role='user',content='Original draft: '+grounded+'\nStyle rewrite: '+styled)
        ],tokenize=False,add_generation_prompt=True,return_dict=False)
        verdict=complete(self.base_model,self.base_tokenizer,check,max_tokens=12,temp=0)
        if not re.match(r'^KEEP\b',verdict,re.I):
            metrics['style_fallback']=True
            return finish(grounded,cfg)
        return finish(styled,cfg)


def generic_ack(text):
    return re.sub(r'[^a-z ]','',text.lower()).strip() in {'i see','ok','okay','indeed','mhm','oh','lol','got it','sounds good'}


def trim_invented_period(reply,turns):
    source=' '.join(re.sub(r'\[(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [^\]]+\]','',t['content']) for t in turns)
    def replace(match):
        number=match[1]
        stated=re.search(r'(?<!\w)'+re.escape(number)+r'(?![\d:])',source)
        explicit=re.search(r'(?<!\w)'+re.escape(number)+r'\s*[ap]m\b',source,re.I)
        return number if stated and not explicit else match[0]
    return re.sub(r'(?<!\w)(\d+(?::\d+)?)\s*([ap]m)\b',replace,reply,flags=re.I)


def unsupported_fact_numbers(reply,turns):
    latest=turns[-1]['content'].lower()
    factual=bool(re.search(r'remind|what (?:time|address)|when (?:is|are|was|were)|where (?:is|are|was|were)|which (?:entrance|gate|notebook)',latest))
    suggestion=bool(re.search(r'should|suggest|do you think|how about|earlier|later',latest))
    if not factual or suggestion:return False
    source=' '.join(re.sub(r'\[(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec) [^\]]+\]','',t['content']) for t in turns)
    numbers=lambda value:set(re.findall(r'(?<!\w)\d+(?::\d+)?',value))
    periods=lambda value:set(re.sub(r'\s','',v) for v in re.findall(r'\d+(?::\d+)?\s*[ap]m\b',value.lower()))
    anchors={'today','tonight','tomorrow','yesterday','monday','tuesday','wednesday','thursday','friday','saturday','sunday','north','south','east','west','main','front','back','blue','red','green','black','white'}
    words=lambda value:set(re.findall(r'[a-z]+',value.lower()))
    return generic_ack(reply) or not numbers(reply).issubset(numbers(source)) or not periods(reply).issubset(periods(source)) or not (words(reply)&anchors).issubset(words(source)&anchors)


def preserves_key_facts(draft,candidate):
    numbers=lambda text:set(re.findall(r'(?<!\w)\d+(?::\d+)?(?:\.\d+)?',text))
    if numbers(draft)!=numbers(candidate):
        return False
    periods=lambda value:set(re.sub(r'\s','',v) for v in re.findall(r'\d+(?::\d+)?\s*[ap]m\b',value.lower()))
    if periods(draft)!=periods(candidate):return False
    temporal={'north','south','east','west','main','front','back','blue','red','green','black','white','today','tonight','tomorrow','yesterday','monday','tuesday','wednesday','thursday','friday','saturday','sunday'}
    words=lambda text:set(re.findall(r"[a-z]+",text.lower()))
    if re.search(r'\b(maybe|suggest|suggested|how about|could|what about)\b',draft,re.I) and not re.search(r'\b(maybe|suggest|suggested|how about|could|what about|would|think)\b',candidate,re.I):return False
    return words(draft)&temporal == words(candidate)&temporal
