"""Surface style for generated replies. Manual Send never calls this module."""
import re

CASUAL_GUIDANCE=("Draft the owner's everyday reply to a friend. Use one brief thought, usually 2 to 12 words, "
    "with natural fragments and contractions. Minimal punctuation, no ending periods, semicolons or em dashes. "
    "Keep normal sentence casing and names. No customer-service phrases, lectures, unprompted offers to help, "
    "or multi-question followups. Stay on their topic; don't append a question unless it's needed to answer. "
    "React naturally to banter. Keep all factual details and uncertainty intact.")


def formal_requested(instructions):
    return bool(re.search(r'\b(formal|professional|professionally|verbatim)\b',instructions,re.I)
        and not re.search(r'\b(informal|casual|less formal|not formal|no formal|avoid formal|not professional)\b|(?:dont|don.t|do not) be formal',instructions,re.I))


def punctuation_requested(instructions):
    keep=re.search(r'\b(?:keep|preserve|exact|use|proper|full)(?: (?:the|full|proper|normal))? punctuation\b',instructions,re.I)
    minimal=re.search(r'\b(?:no|without|avoid|minimal|less|dont use|don.t use|do not use)(?: (?:any|formal|much))? punctuation\b',instructions,re.I)
    return bool(keep and not minimal)


def guidance(cfg):
    instructions=cfg.get('_chat_instructions','')
    if formal_requested(instructions):return ''
    if punctuation_requested(instructions):return CASUAL_GUIDANCE.replace('Minimal punctuation, no ending periods, semicolons or em dashes. ','')
    return CASUAL_GUIDANCE


def finish(text,cfg):
    instructions=cfg.get('_chat_instructions','')
    if formal_requested(instructions) or punctuation_requested(instructions):
        return text
    saved=[]
    def protect(match):
        saved.append(match[0]);return f'\x00{len(saved)-1}\x00'
    # Preserve punctuation/case inside data, code and explicitly quoted text.
    number=r'-?\d+(?::\d+)?(?:\.\d+)?(?:[ \t]*[ap]m)?'
    numeric_range=number+r'[ \t]*[—–][ \t]*'+number
    text=re.sub(r'https?://\S+|www\.\S+|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|`[^`]+`|"[^"\n]+"|'+numeric_range+r'|\b\d+(?:\.\d+)+\b',protect,text,flags=re.I)
    text=re.sub(r'(?:,\s*|\s+)(?:as you (?:asked|requested)|as requested|you know which one I mean|if that helps)[.!?]*$','',text,flags=re.I)
    contractions={
        'I am':'Im','I will':'Ill','I have':'Ive','I would':'Id',
        'it is':'its','that is':'thats','there is':'theres','you are':'youre','we are':"we're",'we will':"we'll",
        'do not':'dont','does not':'doesnt','did not':'didnt','cannot':'cant','can not':'cant',
        'will not':'wont','is not':'isnt','are not':'arent','was not':'wasnt',
        'could not':'couldnt','would not':'wouldnt','should not':'shouldnt',
        "I'm":'Im',"I'll":'Ill',"I've":'Ive',"I'd":'Id',
        "don't":'dont',"doesn't":'doesnt',"didn't":'didnt',"can't":'cant',"won't":'wont',
        "isn't":'isnt',"aren't":'arent',"wasn't":'wasnt',"couldn't":'couldnt',"wouldn't":'wouldnt',"shouldn't":'shouldnt',
        "it's":'its',"that's":'thats',"there's":'theres',"you're":'youre',
    }
    text=text.replace('’',"'")
    pattern=r'\b(?:'+'|'.join(re.escape(k) for k in sorted(contractions,key=len,reverse=True))+r')\b'
    lookup={k.lower():v for k,v in contractions.items()}
    text=re.sub(pattern,lambda m:lookup[m[0].lower()],text,flags=re.I)
    text=re.sub(r'\s*[—–]\s*',lambda m:'-' if m.start()>0 and m.end()<len(text) and text[m.start()-1].isdigit() and text[m.end()].isdigit() else '\n',text)
    text=re.sub(r';\s*','\n',text)
    text=re.sub(r'(?<!\d),(?!\d)','',text)
    text=re.sub(r'!(?=\s|$)','',text)
    text=re.sub(r'[ \t]+',' ',text).strip()
    if text.endswith('.') and not text.endswith('..') and not re.search(r'\b(?:Mr|Mrs|Ms|Dr|St|Jr|Sr|vs|etc|a\.m|p\.m)\.$',text,re.I):
        text=text[:-1]
    if re.search(r'\b(?:all lowercase|lowercase only|use lowercase)\b',instructions,re.I):text=text.lower()
    elif re.search(r'\b(?:all caps|all uppercase)\b',instructions,re.I):text=text.upper()
    else:text=text[:1].upper()+text[1:]
    return re.sub(r'\x00(\d+)\x00',lambda m:saved[int(m[1])],text)
