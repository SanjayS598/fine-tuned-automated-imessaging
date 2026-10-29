import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from imessage_bot import config
from imessage_bot.style_training import style_entry,validate_style,neutralize,export_style
from imessage_bot.policy import OWNER_PROMPT

class StyleTests(unittest.TestCase):
    def test_input_is_derived_only_from_outgoing_target(self):
        entry=style_entry('idk if u wanna meet tmrw at 6',dict(is_from_me=1,message_guid='owner-1'))
        validate_style(entry)
        self.assertIn('tomorrow at 6',entry['messages'][1]['content'])
        for change in ('incoming','foreign_prompt','changed_target'):
            bad=copy.deepcopy(entry)
            if change=='incoming':bad['source']['is_from_me']=0
            elif change=='foreign_prompt':bad['messages'][1]['content']='someone elses message'
            else:bad['messages'][-1]['content']='made up replacement'
            with self.assertRaises(ValueError):validate_style(bad)

    def test_frequent_fillers_and_duplicate_targets_do_not_dominate(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(config,'HOME',Path(directory)):
            texts=['i wanna go\u2028to the library soon']+['I see']*485+[f'i wanna check the plan for item {i}' for i in range(130)]*2
            def fake_export(output_dir,progress):
                output_dir.mkdir()
                records=[dict(messages=[dict(role='system',content='s'),dict(role='user',content=OWNER_PROMPT),dict(role='assistant',content=t)],source=dict(is_from_me=1,message_guid=str(i))) for i,t in enumerate(texts)]
                (output_dir/'train.jsonl').write_text('\n'.join(json.dumps(r) for r in records))
                (output_dir/'valid.jsonl').write_text('')
                return {}
            destination=Path(directory)/'data'
            with patch('imessage_bot.training.export',side_effect=fake_export):export_style(destination)
            splits=[]
            for split in ('train','valid'):
                with (destination/(split+'.jsonl')).open() as stream:splits.append([json.loads(line) for line in stream])
            targets=[{r['messages'][-1]['content'] for r in records} for records in splits]
            self.assertFalse(targets[0]&targets[1])
            self.assertEqual(len(targets[0]|targets[1]),131)
            self.assertNotIn('I see',targets[0]|targets[1])
            for records in splits:
                for record in records:validate_style(record)

if __name__=='__main__':unittest.main()
