import re
from pathlib import Path
from .policy import owner_adapter
from .context import context_prompt,inference_system,CONTEXT_TOKENS


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
        self.base_model = None
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
        def complete(model,prompt,max_tokens=160,temp=.2):
            return self.clean(generate(model,self.tokenizer,prompt=prompt,max_tokens=max_tokens,
                                       sampler=make_sampler(temp=temp),verbose=False))
        progress('reading_context')
        prompt,metrics=context_prompt(self.tokenizer,turns,inference_system(cfg),
                                      cfg.get('context_tokens',CONTEXT_TOKENS))
        metrics['context_messages_available']=cfg.get('_context_message_count',len(turns))
        metrics['style_fallback']=False
        self.last_context=metrics
        if self.base_model is None:
            progress('loading_context_model')
            # The same local base model is used without the personal adapter to
            # preserve conversation understanding. No new model is downloaded.
            self.base_model,_=load(cfg['model'])
        progress('generating_reply')
        grounded=complete(self.base_model,prompt,temp=0)
        if not grounded:
            return ''
        progress('personalizing_reply')
        style_prompt=self.tokenizer.apply_chat_template([
            dict(role='system',content='Rewrite the draft in your learned casual texting style. Keep the EXACT meaning, facts, times, places and intent. Do not add information. If it cannot be rewritten safely, repeat it unchanged. Output only the rewritten text. '+cfg.get('_chat_instructions','')),
            dict(role='user',content='Draft to restyle, without changing its meaning:\n'+grounded)
        ],tokenize=False,add_generation_prompt=True,return_dict=False)
        styled=complete(self.model,style_prompt)
        if not styled or not preserves_key_facts(grounded,styled):
            metrics['style_fallback']=True
            return grounded
        if styled==grounded:
            return grounded
        progress('checking_reply')
        check=self.tokenizer.apply_chat_template([
            dict(role='system',content='Compare a draft and a style rewrite. Output exactly KEEP only if the rewrite preserves all facts, intent, answers and commitments, without adding details or dropping the answer. Otherwise output REJECT. Short wording is fine; changing the answer is not.'),
            dict(role='user',content='Original draft: '+grounded+'\nStyle rewrite: '+styled)
        ],tokenize=False,add_generation_prompt=True,return_dict=False)
        verdict=complete(self.base_model,check,max_tokens=12,temp=0)
        if not re.match(r'^KEEP\b',verdict,re.I):
            metrics['style_fallback']=True
            return grounded
        return styled


def preserves_key_facts(draft,candidate):
    numbers=lambda text:set(re.findall(r'(?<!\w)\d+(?::\d+)?(?:\.\d+)?',text))
    if numbers(draft)!=numbers(candidate):
        return False
    temporal={'today','tonight','tomorrow','yesterday','monday','tuesday','wednesday','thursday','friday','saturday','sunday'}
    words=lambda text:set(re.findall(r"[a-z]+",text.lower()))
    return words(draft)&temporal == words(candidate)&temporal
