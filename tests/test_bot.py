import copy
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from imessage_bot import config
from imessage_bot.messages import Messages, conversation, eligible
from imessage_bot.runner import APPLE_EPOCH, Runner
from imessage_bot.state import State
from imessage_bot.training import examples, fit_example
from imessage_bot.transport import send


def settings():
    return dict(enabled=True, dry_run=True, sender_verified=True,
                allowed_contacts=['+12025550101'], own_identities=['+12025550199'],
                debounce_seconds=8, max_age_seconds=180, cooldown_seconds=30,
                max_per_hour=30, model='test', adapter_path='test', system_prompt='test')


def message(n=1, **changes):
    value = dict(rowid=n, message_guid=f'm-{n}', text='hello', attributedBody=None,
                 is_from_me=0, date=int((time.time()-APPLE_EPOCH)*1e9),
                 associated_message_type=0, item_type=0, chat_id=1,
                 chat_guid='iMessage;-;+12025550101', service='iMessage', style=45,
                 sender='+12025550101', contact='+12025550101', participants=1)
    value.update(changes)
    return value


class FakeMessages:
    def __init__(self):
        self.rows = []

    def max_id(self):
        return max((r['rowid'] for r in self.rows), default=0)

    def new(self, after):
        return [r for r in self.rows if r['rowid'] > after]

    def history(self, chat):
        return [r for r in self.rows if r['chat_id'] == chat]


class BotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.home_patch = patch.object(config, 'HOME', self.home)
        self.home_patch.start()
        self.state = State(self.home/'state.sqlite3')
        self.cfg = settings()
        self.source = FakeMessages()
        self.sent = []
        self.model_hook = lambda: None
        owner = self
        class FakeModel:
            def __init__(self, cfg):
                pass
            def reply(self, turns, cfg):
                owner.model_hook()
                return 'sounds good'
        self.worker = Runner(self.source, self.state, loader=lambda: copy.deepcopy(self.cfg),
                             factory=FakeModel, sender=lambda *args: self.sent.append(args))
        self.worker.tick()  # Establish settings and high-water mark.

    def tearDown(self):
        self.state.close()
        self.home_patch.stop()
        self.tmp.cleanup()

    def receive(self, row=None):
        now = time.time()
        self.source.rows.append(row or message())
        self.worker.tick(now)
        self.worker.tick(now+9)

    def test_preview_never_sends_and_is_persisted(self):
        self.receive()
        self.assertEqual(self.sent, [])
        self.assertEqual(self.state.previews()[0]['status'], 'preview')

    def test_live_sends_once_and_reserves_durably(self):
        self.cfg['dry_run'] = False
        self.worker.tick()
        self.receive()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.state.reserve(message(), 'again', self.cfg, time.time()+40))

    def test_old_inbox_is_never_replayed(self):
        self.source.rows.append(message())
        worker = Runner(self.source, self.state, loader=lambda: self.cfg)
        worker.tick()
        worker.tick(time.time()+30)
        self.assertEqual(len(self.state.previews()), 0)

    def test_pause_discards_incoming(self):
        self.cfg['enabled'] = False
        self.worker.tick()
        self.receive()
        self.cfg['enabled'] = True
        self.worker.tick()
        self.worker.tick(time.time()+20)
        self.assertFalse(self.state.previews())

    def test_allowlist_group_and_self_filter(self):
        for changes in ({'sender':'+12025550102'}, {'participants':2},
                        {'style':43}, {'is_from_me':1}, {'service':'RCS'},
                        {'associated_message_type':2000}, {'text':None}, {'item_type':1}):
            self.assertFalse(eligible(message(**changes), self.cfg), changes)
        self.cfg['own_identities'].append('+12025550101')
        self.assertFalse(eligible(message(), self.cfg))

    def test_per_chat_setting_overrides_contact_allowlist(self):
        self.cfg['chat_settings'] = {'1':dict(enabled=False,instructions='')}
        self.assertFalse(eligible(message(),self.cfg))
        self.cfg['allowed_contacts'] = []
        self.cfg['chat_settings']['1']['enabled'] = True
        self.assertTrue(eligible(message(),self.cfg))
        self.assertFalse(eligible(message(chat_id=2),self.cfg))

    def test_per_chat_instructions_reach_model(self):
        captured = []
        self.cfg['chat_settings'] = {'1':dict(enabled=True,instructions='Use lowercase. Keep it short.')}
        self.worker.tick()
        class CaptureModel:
            def __init__(self,cfg):
                pass
            def reply(self,turns,cfg):
                captured.append(cfg['system_prompt'])
                return 'okay'
        self.worker.factory = CaptureModel
        self.receive()
        self.assertIn('Use lowercase. Keep it short.',captured[0])
        self.assertEqual(self.cfg['system_prompt'],'test')

    def test_stale_message_is_skipped(self):
        self.receive(message(date=int((time.time()-APPLE_EPOCH-600)*1e9)))
        self.assertFalse(self.state.previews())

    def test_manual_reply_while_generating_cancels_send(self):
        self.cfg['dry_run'] = False
        self.worker.tick()
        self.model_hook = lambda: self.source.rows.append(message(2,is_from_me=1))
        self.receive()
        self.assertFalse(self.sent)

    def test_off_during_generation_cancels_send(self):
        self.model_hook = lambda: self.cfg.update(enabled=False)
        self.receive()
        self.assertFalse(self.state.previews())

    def test_contact_removed_during_generation_cancels(self):
        self.model_hook = lambda: self.cfg.update(allowed_contacts=[])
        self.receive()
        self.assertFalse(self.state.previews())

    def test_new_message_during_generation_cancels_stale_reply(self):
        self.model_hook = lambda: self.source.rows.append(message(2,text='wait'))
        self.receive()
        self.assertFalse(self.state.previews())

    def test_burst_is_coalesced(self):
        now = time.time()
        self.source.rows.append(message())
        self.worker.tick(now)
        self.source.rows.append(message(2,text='and another thing'))
        self.worker.tick(now+4)
        self.worker.tick(now+10)
        self.assertFalse(self.state.previews())
        self.worker.tick(now+13)
        self.assertEqual(len(self.state.previews()), 1)

    def test_cooldown_and_hourly_cap(self):
        row = message()
        self.assertTrue(self.state.reserve(row, 'a', self.cfg, 1000))
        self.assertFalse(self.state.reserve(message(2), 'b', self.cfg, 1001))
        self.cfg['max_per_hour'] = 1
        self.assertFalse(self.state.reserve(message(3,chat_id=2), 'c', self.cfg, 1100))

    def test_send_failure_is_not_retried(self):
        self.cfg['dry_run'] = False
        self.worker.tick()
        def fail(*args):
            raise TimeoutError('delivery uncertain')
        self.worker.sender = fail
        with self.assertLogs('imessage_bot.runner', level='ERROR'):
            self.receive()
        self.assertEqual(self.state.previews()[0]['status'], 'failed')
        self.assertFalse(self.state.reserve(message(), 'again', self.cfg, time.time()+40))

    def test_only_generated_live_replies_are_excluded_from_training(self):
        self.state.reserve(message(), 'preview', self.cfg, 100)
        self.assertFalse(self.state.generated(1, 'preview'))
        self.cfg['dry_run'] = False
        self.state.reserve(message(2), 'live', self.cfg, 200)
        self.assertTrue(self.state.generated(1, 'live'))

    def test_training_roles_and_disjoint_sessions(self):
        rows = [message(1,is_from_me=1,text='orphan'),message(2,text='question'),
                message(3,is_from_me=1,text='my reply'),
                message(4,text='another',date=message()['date']+7*3600*10**9),
                message(5,is_from_me=1,text='my second',date=message()['date']+7*3600*10**9)]
        groups = examples(rows, 'system')
        self.assertEqual(len(groups),2)
        self.assertEqual(groups[0][0]['messages'][-1],dict(role='assistant',content='my reply'))
        self.assertNotIn('orphan',json.dumps(groups))
        self.assertNotIn('question',json.dumps(groups[1]))

    def test_training_budget_preserves_assistant_target(self):
        class Tokenizer:
            def apply_chat_template(self, messages, **kwargs):
                return list(''.join(m['content'] for m in messages))
        entry = dict(messages=[dict(role='system',content='s'),
                               dict(role='user',content='u'*20),dict(role='assistant',content='a'*20),
                               dict(role='user',content='new'),dict(role='assistant',content='target')])
        fitted = fit_example(entry,Tokenizer(),limit=20)
        self.assertEqual(len(fitted['messages']),3)
        self.assertEqual(fitted['messages'][-1]['content'],'target')
        self.assertIsNone(fit_example(entry,Tokenizer(),limit=5))

    def test_sqlite_reader_reads_uncheckpointed_wal_and_is_read_only(self):
        path = self.home/'messages.db'
        writer = sqlite3.connect(path)
        try:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE message (guid TEXT)')
            writer.execute("INSERT INTO message VALUES ('first')")
            writer.commit()
            self.assertEqual(Messages(path).max_id(),1)
            with self.assertRaises(sqlite3.OperationalError):
                Messages(path).query('DELETE FROM message')
        finally:
            writer.close()

    def test_real_reader_queries_direct_chat_and_history(self):
        path = self.home/'fixture.db'
        with sqlite3.connect(path) as db:
            db.executescript('''
                CREATE TABLE message (guid TEXT,text TEXT,attributedBody BLOB,is_from_me INTEGER,
                    date INTEGER,associated_message_type INTEGER,item_type INTEGER,handle_id INTEGER);
                CREATE TABLE chat (guid TEXT,service_name TEXT,style INTEGER);
                CREATE TABLE handle (id TEXT);
                CREATE TABLE chat_message_join (chat_id INTEGER,message_id INTEGER);
                CREATE TABLE chat_handle_join (chat_id INTEGER,handle_id INTEGER);
                INSERT INTO chat VALUES ('iMessage;-;+12025550101','iMessage',45);
                INSERT INTO handle VALUES ('+12025550101');
                INSERT INTO chat_handle_join VALUES (1,1);
                INSERT INTO message VALUES ('m-1','question',NULL,0,100,0,0,1);
                INSERT INTO message VALUES ('m-2','my answer',NULL,1,200,0,0,0);
                INSERT INTO chat_message_join VALUES (1,1),(1,2);
            ''')
        reader = Messages(path)
        self.assertEqual(reader.chats()[0]['messages'],2)
        self.assertTrue(eligible(reader.new(0)[0],self.cfg))
        self.assertEqual(conversation(reader.history(1)),[
            dict(role='user',content='question'),dict(role='assistant',content='my answer')])
        self.assertEqual(len(reader.all_history(1)),2)

    def test_applescript_treats_text_as_arguments(self):
        payload = 'hi " & do shell script "example"'
        with patch('imessage_bot.transport.subprocess.run') as run:
            send('iMessage;-;+12025550101',payload)
        argv = run.call_args.args[0]
        self.assertEqual(argv[-1],payload)
        self.assertNotIn(payload,argv[2])

    def test_private_configuration_defaults(self):
        config.initialize('2025550199')
        cfg = config.load()
        self.assertFalse(cfg['enabled'])
        self.assertTrue(cfg['dry_run'])
        self.assertEqual(cfg['allowed_contacts'],[])
        self.assertEqual((self.home/'config.json').stat().st_mode & 0o777,0o600)

    def test_invalid_settings_fail_closed(self):
        config.initialize('2025550199')
        cfg = config.load()
        cfg['enabled'] = 'false'
        config.save(cfg)
        with self.assertRaises(ValueError):
            config.load()


if __name__ == '__main__':
    unittest.main()
