import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_bot import config, contacts, jobs
from imessage_bot.training import examples, parse_report, prepare_entry


class CharacterTokenizer:
    def apply_chat_template(self,messages,**kwargs):
        return list(''.join(m['content'] for m in messages))

    def encode(self,text,**kwargs):
        return list(text)

    def decode(self,tokens):
        return ''.join(tokens)


class ProgressTests(unittest.TestCase):
    def test_long_owner_message_is_split_without_losing_target_content(self):
        target='long reply '*900
        entry=dict(messages=[dict(role='system',content='system'),dict(role='user',content='context '*500),dict(role='assistant',content=target)])
        parts=prepare_entry(entry,CharacterTokenizer())
        self.assertGreater(len(parts),1)
        self.assertEqual(''.join(x['messages'][-1]['content'] for x in parts),target)
        self.assertTrue(all(len(CharacterTokenizer().apply_chat_template(x['messages']))<=1024 for x in parts))

    def test_outbound_first_session_can_be_included_for_full_export(self):
        row=dict(date=800000000000000000,is_from_me=1,text='my first message',attributedBody=None,associated_message_type=0,item_type=0)
        groups=examples([row],'system',include_leading=True)
        self.assertEqual(groups[0][0]['messages'][-1]['content'],'my first message')

    def test_progress_is_real_steps_and_eta_from_native_report(self):
        line='Iter 120: Train loss 2.450, Learning Rate 1.000e-05, It/sec 2.000, Tokens/sec 42.000, Trained Tokens 1234, Peak mem 2.100 GB'
        report=parse_report(line,1000)
        self.assertEqual(report['steps_completed'],120)
        self.assertEqual(report['progress_percent'],12)
        self.assertEqual(report['eta_seconds'],440)
        self.assertEqual(report['train_loss'],2.45)
        self.assertEqual(parse_report('Iter 100: Val loss 2.100, Val took 1s',1000)['validation_loss'],2.1)
        self.assertEqual(parse_report('Iter 500: Saved adapter weights to /private/local',1000)['last_checkpoint_step'],500)

    def test_interrupted_run_retains_previous_progress(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'HOME',Path(folder)):
            jobs.write_status(stage='training',pid=999999999,steps_completed=100,steps_total=1000,progress_percent=10)
            result=jobs.status()
        self.assertEqual(result['stage'],'interrupted')
        self.assertEqual(result['steps_completed'],100)

    def test_contacts_normalize_phone_and_email_without_publishing(self):
        records=[dict(name='Sample Person',addresses=['(202) 555-0101','SAMPLE@EXAMPLE.COM','short']),dict(name='',addresses=['2025550102'])]
        index=contacts.index_contacts(records)
        self.assertEqual(index,{'+12025550101':'Sample Person','sample@example.com':'Sample Person'})


if __name__=='__main__':
    unittest.main()
