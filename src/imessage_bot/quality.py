"""Offline reply checks; these synthetic conversations are never sent or trained."""
from .model import Model,reply_config,generic_ack

CASES=[
    ('agreed time',[('user','lets meet at 6 at the library'),('assistant','sounds good 6 at the library'),('user','what time did we agree?')],['6']),
    ('changed day',[('user','can you meet tonight?'),('assistant','not tonight, tomorrow works'),('user','so tonight or tomorrow?')],['tomorrow']),
    ('known address',[('user','the address is 42 Pine Street'),('assistant','got it'),('user','what address did I give you?')],['42','pine']),
    ('meeting entrance',[('user','meet at the north library entrance'),('assistant','sounds good'),('user','which entrance are we meeting at?')],['north']),
    ('followup',[('user','what time should we hike?'),('assistant','10am maybe?'),('user','lets do earlier'),('assistant','9:30 maybe?'),('user','what was the earlier time you suggested?')],['9:30']),
    ('bring item',[('user','please bring the blue notebook'),('assistant','sure ill bring it'),('user','which notebook are you bringing?')],['blue','notebook']),
    ('missing meeting time',[('user','can you remind me what time the meeting is?')],[]),
]


def evaluate(cfg):
    model=Model(cfg)
    results=[]
    for name,dialog,expected in CASES:
        turns=[dict(role=role,content=content) for role,content in dialog]
        reply=model.reply(turns,reply_config(cfg,0))
        passed=not generic_ack(reply) and all(word in reply.lower() for word in expected)
        if not expected:
            import re
            passed=passed and not re.search(r'\d',reply) and ('?' in reply or any(s in reply.lower() for s in ("don't know",'not sure','not have','need more')))
        results.append(dict(name=name,passed=passed,reply=reply,context=dict(model.last_context)))
    report=dict(passed=sum(r['passed'] for r in results),total=len(results),cases=results)
    return report
