"""Meaningful highlight boundaries and quality signals without a speech download."""
import unittest
from features.clip_studio.engine import clip_engine as engine


def speech(sentences):
    words=[]
    moment=0.0
    for sentence in sentences:
        for text in sentence.split():
            words.append({'w':text,'s':moment,'e':moment+.35})
            moment+=.4
        moment+=.75
    return words


class HighlightSelectionTests(unittest.TestCase):
    def test_complete_story_beats_repeated_promotional_bait(self):
        useful=speech(['Why did our bread keep failing?', 'We learned the problem was cold water.', 'Use warm water instead because it helps the dough rise.'])
        spam=speech(['Secret money profit 999 secret money profit 999 secret money profit 999.'])
        self.assertGreater(engine.score_span(useful)[0],engine.score_span(spam)[0])

    def test_selected_window_has_natural_complete_boundaries_and_hard_limit(self):
        words=speech(['Welcome to our podcast today.', 'How can you make better bread?', 'Use warm water because it helps the dough rise.', 'Try this simple step at home.', 'Thanks for watching and remember to subscribe.'])
        top,chosen=engine.pick_window(words,12)
        starts={id(unit[0]) for unit in engine.split_sentences(words)}
        self.assertIn(id(chosen['span'][0]),starts)
        self.assertTrue(chosen['span'][-1]['w'].endswith(('.', '?', '!')))
        for item in top:
            self.assertLessEqual(item['e']-item['s'],12+1e-8)
            self.assertEqual(item['span'],words[item['i']:item['j']+1])
        self.assertNotEqual(chosen['span'][0]['w'],'Welcome')

    def test_top_suggestions_do_not_overlap_and_are_deterministic(self):
        words=speech(['Why is this useful? Try one clear step.']*8)
        first=engine.pick_window(words,6)
        self.assertEqual(first,engine.pick_window(words,6))
        for i,item in enumerate(first[0]):
            for other in first[0][i+1:]:
                self.assertTrue(item['e']<=other['s'] or item['s']>=other['e'])

    def test_empty_and_invalid_transcripts_fail_helpfully(self):
        for words,target in [([],30),([{'w':'Hi','s':float('nan'),'e':1}],30),(speech(['Hello there.']),0)]:
            with self.assertRaises(SystemExit):engine.pick_window(words,target)

    def test_short_source_is_not_extended_and_long_unpunctuated_speech_fits(self):
        words=speech(['One useful idea.'])
        top,chosen=engine.pick_window(words,30)
        self.assertLessEqual(chosen['e'],words[-1]['e'])
        unpunctuated=speech([' '.join(['useful','bread','recipe','water','mix']*1000)])
        top,chosen=engine.pick_window(unpunctuated,30)
        self.assertLessEqual(chosen['e']-chosen['s'],30+1e-8)
        self.assertTrue(top)


if __name__=='__main__': unittest.main()
