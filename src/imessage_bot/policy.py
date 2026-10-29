"""The fine-tuning policy is independent of live reply context."""
import json
from pathlib import Path

OWNER_POLICY = 'owner_only_v1'
OWNER_PROMPT = 'Write a text message in my usual style.'


def owner_adapter(cfg):
    try:
        folder=Path(cfg['adapter_path'])
        metadata=json.loads((folder/'personal-model.json').read_text())
        return ((folder/'adapters.safetensors').is_file()
                and metadata.get('training_policy')==OWNER_POLICY
                and metadata.get('model')==cfg['model'])
    except (OSError,ValueError,KeyError):
        return False


def validate_owner_entry(entry):
    messages=entry.get('messages',[])
    source=entry.get('source',{})
    if (len(messages)!=3 or [x.get('role') for x in messages]!=['system','user','assistant']
            or messages[1].get('content')!=OWNER_PROMPT
            or source.get('is_from_me')!=1 or not source.get('message_guid')
            or not isinstance(messages[-1].get('content'),str) or not messages[-1]['content'].strip()):
        raise ValueError('Dataset is not owner-only. Export it again; incoming text and historical chat context are forbidden in training.')
