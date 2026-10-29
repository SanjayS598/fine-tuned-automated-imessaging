import unittest
import types
from unittest.mock import patch

from imessage_bot.context import context_prompt
from imessage_bot.model import preserves_key_facts,Model,unsupported_fact_numbers,trim_invented_period
from imessage_bot.style_training import STYLE_POLICY


class Tokenizer:
    def apply_chat_template(self,messages,**kwargs):
        text='\n'.join(m['role']+': '+m['content'] for m in messages)
        if kwargs.get('tokenize',True):
            return list(text) if kwargs.get('return_dict') is False else {'input_ids':list(text)}
        return text


class ContextTests(unittest.TestCase):
    def test_generic_style_rewrite_cannot_drop_a_specific_answer(self):
        model=object.__new__(Model)
        model.model=object();model.tokenizer=Tokenizer()
        model.base_model=object();model.base_tokenizer=model.tokenizer
        model.style_policy=STYLE_POLICY
        replies=iter(['the north entrance','I see'])
        fake=types.SimpleNamespace(generate=lambda *a,**k:next(replies),load=lambda *a:None)
        sampler=types.SimpleNamespace(make_sampler=lambda **k:None)
        with patch.dict('sys.modules',{'mlx_lm':fake,'mlx_lm.sample_utils':sampler}):
            result=model.reply([dict(role='user',content='meet at the north entrance'),dict(role='assistant',content='okay'),dict(role='user',content='which entrance?')],{})
        self.assertEqual(result,'the north entrance')
        self.assertTrue(model.last_context['style_fallback'])

    def test_unknown_fact_cannot_borrow_a_time_from_message_metadata(self):
        turns=[dict(role='user',content='[Oct 29 14:00] can you remind me what time the meeting is?')]
        self.assertTrue(unsupported_fact_numbers('The meeting is at 14:00',turns))
        self.assertFalse(unsupported_fact_numbers('What time was agreed on?',turns))
        self.assertTrue(unsupported_fact_numbers('We agreed 6 pm',[dict(role='user',content='we said 6. what time was it?')]))
        self.assertFalse(preserves_key_facts('meet at 6','meet at 6 pm'))
        self.assertEqual(trim_invented_period('meet at 6 PM',[dict(role='user',content='we agreed on 6')]),'meet at 6')
        self.assertEqual(trim_invented_period('meet at 6 PM',[dict(role='user',content='we agreed on 6 pm')]),'meet at 6 PM')
        self.assertFalse(preserves_key_facts('the north entrance','the main entrance'))
        self.assertFalse(preserves_key_facts('I suggested 9:30','9:30 sounds good for us'))

    def test_reply_does_not_copy_context_timestamp(self):
        self.assertEqual(Model.clean('[Oct 29 12:11] sounds good'),'sounds good')
        self.assertEqual(Model.clean('meet at [the library]'),'meet at [the library]')

    def test_budget_preserves_latest_question_and_system_instructions(self):
        turns=[]
        for i in range(120):
            turns.append(dict(role='user' if i%2==0 else 'assistant',content='history '+str(i)+' '*20))
        turns.append(dict(role='user',content='LATEST QUESTION: what time did we agree?'))
        prompt,metrics=context_prompt(Tokenizer(),turns,'OWNER INSTRUCTIONS',limit=600)
        self.assertLessEqual(metrics['context_tokens'],600)
        self.assertIn('OWNER INSTRUCTIONS',prompt)
        self.assertIn('LATEST QUESTION',prompt)
        self.assertIn('history 119',prompt)
        self.assertNotIn('history 0 ',prompt)

    def test_long_latest_message_is_bounded_without_losing_its_edges(self):
        turns=[dict(role='user',content='START '+('long text '*1000)+' END')]
        prompt,metrics=context_prompt(Tokenizer(),turns,'system',limit=400)
        self.assertLessEqual(metrics['context_tokens'],400)
        self.assertIn('START',prompt)
        self.assertIn('END',prompt)

    def test_oversized_instructions_fail_instead_of_looping(self):
        with self.assertRaises(ValueError):
            context_prompt(Tokenizer(),[dict(role='user',content='hi')],'x'*1000,limit=100)

    def test_personal_style_cannot_change_time_or_day(self):
        self.assertFalse(preserves_key_facts('we agreed on 6','we agreed on 7:30'))
        self.assertFalse(preserves_key_facts('let us meet tomorrow','then later'))
        self.assertTrue(preserves_key_facts('meet tomorrow at 6 pm','tmrw? tomorrow 6pm'))


if __name__=='__main__':
    unittest.main()
