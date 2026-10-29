import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_bot import config
from imessage_bot.model import Model
from imessage_bot.policy import OWNER_POLICY, OWNER_PROMPT, owner_adapter, validate_owner_entry
from imessage_bot.training import examples, train


class OwnerOnlyTests(unittest.TestCase):
    def test_incoming_content_never_enters_prompts_or_targets(self):
        def row(own,text,date):
            return dict(is_from_me=own,text=text,date=date,attributedBody=None,
                        associated_message_type=0,item_type=0,message_guid=f'guid-{date}')
        incoming='UNIQUE_OTHER_PERSON_CONTENT_DO_NOT_TRAIN'
        rows=[row(0,incoming,1),row(1,'my actual words',2),row(0,incoming,3),row(1,'my second reply',4)]
        with patch('imessage_bot.training.decode_text',side_effect=lambda r:r['text']) as decode:
            groups=examples(rows,'system')
        self.assertTrue(all(call.args[0]['is_from_me']==1 for call in decode.call_args_list))
        self.assertNotIn(incoming,json.dumps(groups))
        entries=[e for group in groups for e in group]
        self.assertEqual(len(entries),2)
        for entry in entries:
            validate_owner_entry(entry)
            self.assertEqual(entry['messages'][1]['content'],OWNER_PROMPT)

    def test_mixed_or_unproven_examples_are_rejected(self):
        entry=dict(messages=[dict(role='system',content='system'),dict(role='user',content=OWNER_PROMPT),dict(role='assistant',content='my words')],source=dict(is_from_me=1,message_guid='mine'))
        validate_owner_entry(entry)
        foreign=copy.deepcopy(entry)
        foreign['messages'][1]['content']='text from another person'
        with self.assertRaises(ValueError):validate_owner_entry(foreign)
        foreign=copy.deepcopy(entry)
        foreign['source']['is_from_me']=0
        with self.assertRaises(ValueError):validate_owner_entry(foreign)
        del entry['source']
        with self.assertRaises(ValueError):validate_owner_entry(entry)

    def test_legacy_datasets_cannot_start_training(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'HOME',Path(folder)):
            config.initialize('2025550199')
            data=Path(folder)/'data'
            data.mkdir()
            for split in ('train','valid'):
                (data/(split+'.jsonl')).write_text('{}\n')
            with self.assertRaisesRegex(ValueError,'Owner-only'):
                train(1)

    def test_mixed_context_adapter_cannot_be_loaded(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)
            cfg=dict(adapter_path=folder,model='test')
            (path/'adapters.safetensors').touch()
            (path/'personal-model.json').write_text(json.dumps(dict(model='test')))
            self.assertFalse(owner_adapter(cfg))
            with self.assertRaisesRegex(ValueError,'owner-only'):
                Model(cfg)
            (path/'personal-model.json').write_text(json.dumps(dict(model='test',training_policy=OWNER_POLICY)))
            self.assertTrue(owner_adapter(cfg))


if __name__=='__main__':
    unittest.main()
