"""Bounded inference context. This module is never used by training/export."""
from datetime import datetime

CONTEXT_MESSAGES = 120
CONTEXT_TOKENS = 8192
REPLY_CONTEXT_MESSAGES = 8
REPLY_CONTEXT_TOKENS = 3072


def context_prompt(tokenizer,turns,system,limit=CONTEXT_TOKENS):
    messages=[dict(role='system',content=system)]+[dict(turn) for turn in turns]
    def tokens():
        return tokenizer.apply_chat_template(messages,tokenize=True,add_generation_prompt=True,return_dict=False)
    while len(tokens())>limit and len(messages)>3:
        del messages[1:3]
    while len(tokens())>limit:
        candidates=[i for i in range(1,len(messages)) if len(messages[i]['content'])>16]
        if not candidates:
            raise ValueError('The system instructions exceed the conversation context budget.')
        index=max(candidates,key=lambda i:len(messages[i]['content']))
        value=messages[index]['content']
        if len(value)<96:
            messages[index]['content']=value[-8:]
            continue
        # Keep both the beginning and end of a very long recent message.
        keep=max(8,len(value)//4)
        messages[index]['content']=value[:keep]+'\n[long message shortened]\n'+value[-keep:]
    count=len(tokens())
    prompt=tokenizer.apply_chat_template(messages,tokenize=False,add_generation_prompt=True,return_dict=False)
    return prompt,dict(context_turns=len(messages)-1,context_tokens=count,context_token_limit=limit)


def inference_system(cfg):
    now=datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')
    result=('Write only a short text reply to the latest message. '
        'Use facts from the conversation. Answer the question directly. '
        'Keep accepted plans accurate; a suggestion is not an agreement. '
        'When asked for a suggestion, you may offer one, clearly as a suggestion. '
        'Do not invent the owner’s availability, location or actions. If a personal fact is unknown, ask a short clarifying question. '
        'Avoid generic acknowledgments when the message asks a question. Keep it casual and natural. '
        'Bracketed timestamps are context metadata; never include them in your reply. '
        f'Current local time: {now}. Dated older activities are background, not current facts. ')
    if cfg.get('_is_group'):
        result+='This is a group conversation. Reply as the owner; do not impersonate other participants or use artificial Participant labels in your reply. '
    if cfg.get('_chat_instructions'):
        result+="Owner's instructions for this conversation: "+cfg['_chat_instructions']
    return result
