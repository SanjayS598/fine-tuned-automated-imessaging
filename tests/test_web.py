import http.client
import json
import sqlite3
import tempfile
import threading
import types
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from imessage_bot import config
from imessage_bot.inbox import Inbox
from imessage_bot.web import App, Handler


class InboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.patch = patch.object(config,'HOME',self.home)
        self.patch.start()
        config.initialize('2025550199')
        self.path = self.home/'messages.db'
        with sqlite3.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE message (guid TEXT,text TEXT,attributedBody BLOB,is_from_me INTEGER,
                    date INTEGER,associated_message_type INTEGER,item_type INTEGER,handle_id INTEGER,
                    is_read INTEGER,is_delivered INTEGER,is_sent INTEGER,cache_has_attachments INTEGER,
                    associated_message_guid TEXT,subject TEXT);
                CREATE TABLE chat (guid TEXT,service_name TEXT,style INTEGER,display_name TEXT,chat_identifier TEXT);
                CREATE TABLE handle (id TEXT);
                CREATE TABLE chat_message_join (chat_id INTEGER,message_id INTEGER);
                CREATE TABLE chat_handle_join (chat_id INTEGER,handle_id INTEGER);
                CREATE TABLE attachment (filename TEXT,transfer_name TEXT,mime_type TEXT,total_bytes INTEGER);
                CREATE TABLE message_attachment_join (message_id INTEGER,attachment_id INTEGER);
                INSERT INTO chat VALUES ('iMessage;-;+12025550101','iMessage',45,'','+12025550101');
                INSERT INTO chat VALUES ('iMessage;+;group','iMessage',43,'Weekend group','group');
                INSERT INTO chat VALUES ('RCS;-;+12025550102','RCS',45,'','+12025550102');
                INSERT INTO handle VALUES ('+12025550101'),('+12025550102');
                INSERT INTO chat_handle_join VALUES (1,1),(2,1),(2,2),(3,2);
                INSERT INTO attachment VALUES ('/etc/passwd','private.txt','text/plain',10);
            ''')
            for i in range(1,76):
                db.execute('INSERT INTO message VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (f'm-{i}',f'message {i}',None,i%2,800000000000000000+i,0,0,1,0,1,1,0,None,None))
                db.execute('INSERT INTO chat_message_join VALUES (1,?)',(i,))
            db.execute('INSERT INTO message_attachment_join VALUES (75,1)')
        config.update(lambda c:c.update(db_path=str(self.path)))
        self.app=App(0)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.app.port=self.server.server_port
        self.server.app=self.app
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.patch.stop()
        self.tmp.cleanup()

    def request(self,path,body=None,auth=True,origin=True,host=None):
        client=http.client.HTTPConnection('127.0.0.1',self.app.port)
        headers={}
        if auth:
            headers['Cookie']='inbox_session='+self.app.token
        if body is not None:
            headers['Content-Type']='application/json'
            if origin:
                headers['Origin']=self.app.origin
        if host:
            headers['Host']=host
        client.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),headers)
        response=client.getresponse()
        data=response.read()
        status=response.status
        mime=response.headers.get('Content-Type','')
        headers=dict(response.headers)
        client.close()
        return status,json.loads(data) if mime=='application/json' else data,headers

    def test_all_chats_including_groups_and_rcs_are_visible(self):
        status,data,_=self.request('/api/chats')
        self.assertEqual(status,200)
        self.assertEqual(len(data['chats']),3)
        by_id={x['id']:x for x in data['chats']}
        self.assertTrue(by_id[1]['reply_supported'])
        self.assertTrue(by_id[2]['reply_supported'])
        self.assertTrue(by_id[3]['reply_supported'])

    def test_synced_contacts_replace_numbers_but_preserve_addresses(self):
        from imessage_bot import contacts
        contacts.save(stage='connected',count=1,index={'+12025550101':'Sample Person'})
        _,data,_=self.request('/api/chats')
        chat=next(x for x in data['chats'] if x['id']==1)
        self.assertEqual(chat['title'],'Sample Person')
        self.assertEqual(chat['participants'],['+12025550101'])
        _,status,_=self.request('/api/status')
        self.assertNotIn('index',status['contacts'])

    def test_training_source_includes_group_and_rcs_chats(self):
        from imessage_bot.messages import Messages
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO chat_message_join VALUES (2,1),(3,3)')
        source=Messages(self.path)
        self.assertEqual({x['chat_id'] for x in source.training_chats()},{1,2,3})
        self.assertEqual(source.orphan_history(),[])
        self.assertTrue(all(x['is_from_me']==1 for x in source.training_history(1)))
        self.assertEqual(len(source.training_history(1)),38)

    def test_training_blocks_enabling_replies(self):
        with patch('imessage_bot.web.jobs.status',return_value=dict(stage='training')):
            status,data,_=self.request('/api/control',dict(enabled=True))
        self.assertEqual(status,400)
        self.assertIn('training',data['error'])

    def test_message_pagination_has_no_gaps(self):
        inbox=Inbox(self.path)
        recent=inbox.messages(1)
        self.assertEqual(len(recent['messages']),60)
        self.assertTrue(recent['has_more'])
        older=inbox.messages(1,before=recent['messages'][0]['id'])
        all_ids=[x['id'] for x in older['messages']+recent['messages']]
        self.assertEqual(all_ids,list(range(1,76)))
        self.assertFalse(older['has_more'])
        self.assertEqual(len(inbox.messages(1,after=70)['messages']),5)
        self.assertEqual(recent['messages'][-1]['attachments'][0]['name'],'private.txt')

    def test_chat_toggle_and_instructions_persist_independently(self):
        status,data,_=self.request('/api/chats/1',dict(enabled=True,instructions='Be brief.'))
        self.assertEqual(status,200)
        self.assertTrue(data['auto_reply'])
        self.assertEqual(data['instructions'],'Be brief.')
        cfg=config.load()
        self.assertTrue(cfg['chat_settings']['1']['enabled'])
        self.assertFalse(cfg['enabled'])
        self.assertEqual(cfg['allowed_contacts'],[])
        self.assertEqual(self.request('/api/chats/1',dict(enabled=False))[0],200)
        self.assertEqual(config.load()['chat_settings']['1']['instructions'],'Be brief.')

    def test_groups_and_rcs_can_be_enabled_independently(self):
        self.assertEqual(self.request('/api/chats/2',dict(enabled=True))[0],200)
        self.assertEqual(self.request('/api/chats/3',dict(enabled=True))[0],200)
        self.assertNotIn('1',config.load()['chat_settings'])
        self.assertFalse(config.load()['enabled'])

    def test_group_is_not_enabled_by_member_allowlist(self):
        config.update(lambda c:c.update(allowed_contacts=['+12025550101']))
        _,data,_=self.request('/api/chats')
        by_id={x['id']:x for x in data['chats']}
        self.assertTrue(by_id[1]['auto_reply'])
        self.assertFalse(by_id[2]['auto_reply'])

    def test_historical_manual_preview_cannot_send_and_keeps_config(self):
        class FakeModel:
            def __init__(self,cfg):pass
            def reply(self,turns,cfg):return 'preview reply'
        before=config.load()
        with patch('imessage_bot.web.owner_adapter',return_value=True),patch('imessage_bot.model.Model',FakeModel),patch('imessage_bot.transport.send') as send:
            status,_,_=self.request('/api/chats/1/preview',{})
            self.assertEqual(status,202)
            import time
            for _ in range(100):
                if self.app.preview_job['stage'] in ('ready','failed'):break
                time.sleep(.01)
            self.assertEqual(self.app.preview_job['stage'],'ready')
            _,data,_=self.request('/api/chats/1/preview')
            self.assertEqual(data['preview']['reply'],'preview reply')
            self.assertEqual(data['preview']['status'],'manual_preview')
            send.assert_not_called()
        self.assertEqual(config.load(),before)

    def test_untrusted_origins_and_hosts_are_rejected(self):
        self.assertEqual(self.request('/api/chats/1',dict(enabled=True),origin=False)[0],403)
        self.assertEqual(self.request('/api/status',host='evil.example')[0],403)
        self.assertEqual(self.request('/api/status',auth=False)[0],401)

    def test_session_bootstrap_uses_http_only_strict_cookie(self):
        status,_,headers=self.request('/?token='+self.app.token,auth=False)
        self.assertEqual(status,303)
        self.assertIn('HttpOnly',headers['Set-Cookie'])
        self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertEqual(headers['Location'],'/')
        self.assertEqual(self.request('/?token=bad',auth=False)[0],403)

    def test_attachment_cannot_read_arbitrary_local_files(self):
        self.assertEqual(self.request('/api/attachments/1')[0],404)

    def test_invalid_and_oversized_chat_settings_are_rejected(self):
        for body in (dict(enabled='true'),dict(instructions='x'*4001),dict(instructions=3),dict(surprise=True)):
            self.assertEqual(self.request('/api/chats/1',body)[0],400)

    def test_master_switch_requires_trained_model(self):
        status,body,_=self.request('/api/control',dict(enabled=True))
        self.assertEqual(status,400)
        self.assertIn('Train',body['error'])
        self.assertFalse(config.load()['enabled'])

    def test_packaged_app_explains_missing_messages_permission(self):
        config.update(lambda c:c.update(db_path=str(self.home/'missing.db'),packaged_data=True))
        status,data,_=self.request('/api/status')
        self.assertEqual(status,200)
        self.assertFalse(data['connected'])
        self.assertIn('Enable Inbox',data['error'])
        self.assertEqual(self.request('/api/chats')[0],503)
        self.assertEqual(self.request('/api/control',dict(enabled=True))[0],400)

    def test_static_assets_have_private_browser_policy(self):
        status,body,headers=self.request('/')
        self.assertEqual(status,200)
        self.assertIn(b'Reply settings',body)
        self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])
        self.assertEqual(headers['Cache-Control'],'no-store')
        self.assertEqual(self.request('/app.js')[0],200)
        self.assertEqual(self.request('/style.css')[0],200)

    def test_unicode_line_separators_do_not_break_training_json(self):
        from imessage_bot.training import train
        from imessage_bot.policy import OWNER_POLICY, OWNER_PROMPT
        data=self.home/'data'
        data.mkdir()
        example=dict(messages=[dict(role='system',content=config.load()['system_prompt']),dict(role='user',content=OWNER_PROMPT),dict(role='assistant',content='hi\u2028there\u0085again')],source=dict(is_from_me=1,message_guid='own-1'))
        (data/'manifest.json').write_text(json.dumps(dict(training_policy=OWNER_POLICY,incoming_messages_included=0)))
        for name in ('train','valid'):
            (data/(name+'.jsonl')).write_text(json.dumps(example,ensure_ascii=False)+'\n')
        tokenizer=types.SimpleNamespace(apply_chat_template=lambda *args,**kwargs:[1,2,3])
        fake=types.SimpleNamespace(AutoTokenizer=types.SimpleNamespace(from_pretrained=lambda *args:tokenizer))
        with patch.dict('sys.modules',{'transformers':fake}),patch('imessage_bot.training.subprocess.run'):
            train(1)
        prepared=json.loads((self.home/'prepared_data/train.jsonl').read_text())
        self.assertEqual(prepared['messages'][2]['content'],'hi\u2028there\u0085again')


if __name__=='__main__':
    unittest.main()
