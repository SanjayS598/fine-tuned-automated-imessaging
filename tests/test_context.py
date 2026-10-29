import unittest

from imessage_bot.context import context_prompt
from imessage_bot.model import preserves_key_facts,Model


class Tokenizer:
    def apply_chat_template(self,messages,**kwargs):
        text='\n'.join(m['role']+': '+m['content'] for m in messages)
        if kwargs.get('tokenize',True):
            return list(text) if kwargs.get('return_dict') is False else {'input_ids':list(text)}
        return text


class ContextTests(unittest.TestCase):
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
