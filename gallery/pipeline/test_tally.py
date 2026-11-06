import unittest

from tally import corrections_for, tally


class TallyTest(unittest.TestCase):
    def test_weights_settlement_disputes_and_ignored_votes(self):
        questions = [
            {"id": "f1:0", "type": "face", "cands": ["S1"], "photo": "1", "i": 0},
            {"id": "c2", "type": "cluster", "cands": [], "faces": ["2:0", "3:0"]},
            {"id": "s4", "type": "scene", "photo": "4"},
        ]
        users = {"self": {"status": "approved", "pid": "S1"}, "other": {"status": "approved", "pid": "S2"}, "pending": {"status": "pending", "pid": "S3"}}
        votes = [
            {"qid": "f1:0", "uid": "self", "answer": "S1"},
            {"qid": "f1:0", "uid": "other", "answer": "S1"},
            {"qid": "f1:0", "uid": "pending", "answer": "S1"},
            *[{"qid": "c2", "uid": "other", "answer": a} for a in ("A", "A", "B", "B", "C")],
            *[{"qid": "s4", "uid": "other", "answer": "skip"} for _ in range(5)],
        ]
        self.assertEqual(tally(questions, votes, users), {"settled": {"f1:0": "S1"}, "disputed": ["c2"]})

    def test_corrections_match_review_server_shape(self):
        questions = [
            {"id": "c1", "type": "cluster", "faces": ["p:0", "p:1"]},
            {"id": "s2", "type": "scene", "photo": "p"},
        ]
        got = corrections_for(questions, {"c1": "S1", "s2": "campus"}, {})
        self.assertEqual(got, {"faces": {"p:0": "S1", "p:1": "S1"}, "scenes": {"p": "campus"}, "skipped": [], "history": []})

    def test_override_wins_over_votes_and_settles_question(self):
        questions = [{"id": "f1:0", "type": "face", "cands": [], "photo": "1", "i": 0}]
        votes = [{"qid": "f1:0", "uid": "u", "answer": "wrong"}]
        users = {"u": {"status": "approved", "pid": "S1"}}
        self.assertEqual(tally(questions, votes, users, {"f1:0": "S2"}), {"settled": {"f1:0": "S2"}, "disputed": []})


if __name__ == "__main__":
    unittest.main()
