import unittest
from imessage_bot.texting import finish,guidance

class TextingTests(unittest.TestCase):
    def test_casual_contractions_and_light_punctuation_keep_the_answer(self):
        self.assertEqual(finish("I cannot meet at 9:30.",{}),'I cant meet at 9:30')
        self.assertEqual(finish("Yeah, it's pretty bad.",{}),'Yeah its pretty bad')
        self.assertEqual(finish("I'm leaving tomorrow; I'll bring the blue notebook.",{}),'Im leaving tomorrow\nIll bring the blue notebook')
        self.assertEqual(finish('How about 9:30?',{}),'How about 9:30?')
        self.assertEqual(finish('The blue one as you asked.',{}),'The blue one')
        self.assertEqual(finish('The north one you know which one I mean.',{}),'The north one')

    def test_sensitive_punctuation_names_and_data_are_preserved(self):
        self.assertEqual(finish('42 Pine Dr.',{}),'42 Pine Dr.')
        self.assertEqual(finish('Pay $1,000 for version 1.2.3.',{}),'Pay $1,000 for version 1.2.3')
        self.assertEqual(finish('From 5–10.',{}),'From 5–10')
        self.assertEqual(finish('From -5.5–10.5.',{}),'From -5.5–10.5')
        self.assertEqual(finish('Meet 5 PM – 6 PM.',{}),'Meet 5 PM – 6 PM')
        self.assertEqual(finish('Email A.B+Tag@Example.com.',{}),'Email A.B+Tag@Example.com')
        self.assertEqual(finish('Use https://Example.com/Case?A=1&B=2',{}),'Use https://Example.com/Case?A=1&B=2')
        self.assertEqual(finish('Type `dont();`.',{}),'Type `dont();`')

    def test_per_chat_style_requests_override_the_default(self):
        formal=dict(_chat_instructions='Use a professional tone')
        self.assertEqual(guidance(formal),'')
        self.assertEqual(finish('I cannot attend.',formal),'I cannot attend.')
        self.assertTrue(guidance(dict(_chat_instructions='Keep it informal')))
        for instructions in ('Dont use punctuation','No formal punctuation'):
            cfg=dict(_chat_instructions=instructions)
            self.assertTrue(guidance(cfg))
            self.assertEqual(finish('Yeah, it is fine.',cfg),'Yeah its fine')
        self.assertEqual(finish('I can attend.',dict(_chat_instructions='Use full punctuation')),'I can attend.')
        self.assertEqual(finish('Meet at Pine Street. Use https://Example.com/A',dict(_chat_instructions='all lowercase')),'meet at pine street. use https://Example.com/A')

if __name__=='__main__':unittest.main()
