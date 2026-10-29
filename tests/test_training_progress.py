import tempfile
import json
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_bot import config, contacts, jobs
from imessage_bot.training import examples, parse_report, prepare_entry
from imessage_bot.policy import OWNER_POLICY, OWNER_PROMPT


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

    def test_invalid_loss_stops_instead_of_freezing_progress(self):
        for value in ('nan','inf','-inf'):
            line=f'Iter 60: Train loss {value}, Learning Rate 1e-5, It/sec 2.000, Tokens/sec 42.000, Trained Tokens 1234, Peak mem 2.100 GB'
            with self.assertRaises(ValueError):
                parse_report(line,1000)
        with self.assertRaises(ValueError):
            parse_report('Iter 100: Val loss nan, Val took 1s',1000)

    def test_token_budget_requests_token_list_with_new_transformers(self):
        class NewTokenizer(CharacterTokenizer):
            def apply_chat_template(self,messages,**kwargs):
                tokens=super().apply_chat_template(messages,**kwargs)
                return tokens if kwargs.get('return_dict') is False else dict(input_ids=tokens,attention_mask=[1]*len(tokens))
        target='owner reply '*500
        entry=dict(messages=[dict(role='system',content='s'),dict(role='user',content='u'*4000),dict(role='assistant',content=target)])
        parts=prepare_entry(entry,NewTokenizer())
        self.assertGreater(len(parts),1)
        self.assertEqual(''.join(x['messages'][-1]['content'] for x in parts),target)
        self.assertTrue(all(len(NewTokenizer().apply_chat_template(x['messages'],return_dict=False))<=1024 for x in parts))

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

    def test_default_training_steps_cover_every_prepared_example(self):
        from imessage_bot.training import train
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'HOME',Path(folder)):
            config.initialize('2025550199')
            data=Path(folder)/'data'
            data.mkdir()
            example=dict(messages=[dict(role='system',content=config.load()['system_prompt']),dict(role='user',content=OWNER_PROMPT),dict(role='assistant',content='reply')],source=dict(is_from_me=1,message_guid='own-1'))
            (data/'manifest.json').write_text(json.dumps(dict(training_policy=OWNER_POLICY,incoming_messages_included=0)))
            (data/'train.jsonl').write_text((json.dumps(example)+'\n')*3)
            (data/'valid.jsonl').write_text(json.dumps(example)+'\n')
            fake=types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *args:CharacterTokenizer()))
            with patch.dict('sys.modules',{'transformers':fake}),patch('imessage_bot.training.subprocess.run') as run:
                metadata=train()
            argv=run.call_args.args[0]
            self.assertEqual(argv[argv.index('--iters')+1],'3')
            self.assertEqual(metadata['passes'],1)

    def test_failed_retraining_preserves_previous_model(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'HOME',Path(folder)):
            config.initialize('2025550199')
            config.update(lambda c:c.update(training_recipe='owner_only_v1'))
            original=config.load()['adapter_path']
            with patch('imessage_bot.training.export'),patch('imessage_bot.training.train',side_effect=RuntimeError('test')):
                with self.assertRaises(RuntimeError):
                    jobs.main()
            self.assertEqual(config.load()['adapter_path'],original)
            self.assertEqual(jobs.status()['stage'],'failed')

    def test_successful_training_promotes_only_finished_version(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'HOME',Path(folder)):
            config.initialize('2025550199')
            config.update(lambda c:c.update(training_recipe='owner_only_v1'))
            original=config.load()['adapter_path']
            def completed(**kwargs):
                self.assertEqual(config.load()['adapter_path'],original)
                return dict(iterations=3)
            with patch('imessage_bot.training.export'),patch('imessage_bot.training.train',side_effect=completed):
                jobs.main()
            self.assertNotEqual(config.load()['adapter_path'],original)
            self.assertFalse(config.load()['enabled'])
            self.assertEqual(jobs.status()['progress_percent'],100)

    def test_style_candidate_must_pass_answer_checks_before_activation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'HOME',Path(folder)):
            config.initialize('2025550199')
            original=config.load()['adapter_path']
            with patch('imessage_bot.style_training.export_style'),patch('imessage_bot.training.train',return_value=dict(iterations=600)),patch('imessage_bot.quality.evaluate',return_value=dict(passed=0,total=6)):
                with self.assertRaisesRegex(ValueError,'reply checks'):jobs.main()
            self.assertEqual(config.load()['adapter_path'],original)
            self.assertFalse(config.load()['enabled'])
            self.assertEqual(jobs.status()['stage'],'failed')


if __name__=='__main__':
    unittest.main()
