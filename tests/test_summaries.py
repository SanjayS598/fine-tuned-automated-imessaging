import json
import unittest
from imessage_bot.summaries import parse, fingerprint,windows


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.rows=[dict(id=1,owner=True,text='how about 9:30?',date=1),dict(id=2,owner=False,text='yes 9:30 works',date=2)]

    def test_one_sided_bot_suggestion_is_not_a_confirmed_agreement(self):
        items=parse(json.dumps(dict(items=[dict(kind='agreed',text='9:30 proposed',evidence=[1])])),self.rows,{1})
        self.assertEqual(items[0]['kind'],'proposed')
        self.assertTrue(items[0]['bot_involved'])
        self.assertEqual(items[0]['evidence'][0]['text'],'how about 9:30?')

    def test_both_sides_support_agreement_and_unknown_evidence_is_rejected(self):
        items=parse(json.dumps(dict(items=[dict(kind='agreed',text='9:30 agreed',evidence=[1,2]),dict(kind='open',text='invented',evidence=[900])])),self.rows)
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['kind'],'agreed')

    def test_incoming_text_alone_cannot_create_owner_commitment(self):
        item=parse(json.dumps(dict(items=[dict(kind='commitment',text='suggested time',evidence=[2])])),self.rows)[0]
        self.assertEqual(item['kind'],'proposed')

    def test_content_change_invalidates_cached_summary(self):
        original=fingerprint(self.rows)
        self.rows[1]['text']='actually lets do 8 instead'
        self.assertNotEqual(original,fingerprint(self.rows))

    def test_windows_cover_history_newest_first_with_overlap(self):
        rows=list(range(120))
        batches=windows(rows)
        self.assertEqual(batches[0],list(range(112,120)))
        self.assertEqual(set(x for batch in batches for x in batch),set(rows))
        self.assertTrue(all(len(batch)<=8 for batch in batches))
        self.assertTrue(set(batches[0])&set(batches[1]))

    def test_summary_cannot_invent_a_day_or_time_its_sources_do_not_contain(self):
        for title in ('Friday at 9:30','Meet at 10:30'):
            with self.assertRaises(ValueError):
                parse(json.dumps(dict(items=[dict(kind='proposed',text=title,evidence=[1])])),self.rows)

    def test_empty_and_invalid_output(self):
        self.assertEqual(parse('{"items":[]}',self.rows),[])
        with self.assertRaises(ValueError):parse('unstructured answer',self.rows)

    def test_banter_is_not_an_open_plan_question(self):
        rows=[dict(id=1,owner=False,text='lol',date=1)]
        with self.assertRaises(ValueError):
            parse('{"items":[{"kind":"open","text":"Unclear response","evidence":[1]}]}',rows)

    def test_local_model_missing_key_quotes_keeps_source_validation(self):
        items=parse('{"items":[{"kind":"open","text":"When to hike?",evidence:[1,2]}]}',self.rows)
        self.assertEqual(items[0]['kind'],'open')
        self.assertEqual(len(items[0]['evidence']),2)


if __name__=='__main__':unittest.main()
