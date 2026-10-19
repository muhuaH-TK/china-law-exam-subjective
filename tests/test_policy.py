import copy
import unittest

from lawstudy.policy import daily_plan, due_cards, next_due, review_card, stage, subjects, theme_group
from lawstudy.store import blank_instance


def prepared(number=None, elective="administrative"):
    data = blank_instance()
    data["lifecycle"] = "active"
    data["config"]["usage_mode"] = "formal"
    data["desktop"] = {"host_project": {"status": "skipped"}}
    data["config"].update(year=2027, participation="participating", elective=elective, stage_override=number)
    for key, day in {"objective_end":"2027-09-12", "results":"2027-09-18", "subjective":"2027-10-17"}.items():
        data["config"]["exam_dates"][key].update(date=day, status="verified")
    return data


def card():
    return {"id":"r1", "subject":"刑法", "first_error_on":"2027-09-01", "status":"waiting", "reviews":[]}


class PolicyTests(unittest.TestCase):
    def test_exact_stage_boundaries(self):
        config = prepared()["config"]
        for day, expected in [("2027-09-12",1),("2027-09-13",2),("2027-09-18",2),
                              ("2027-09-19",3),("2027-10-09",3),("2027-10-10",4),("2027-10-16",4)]:
            with self.subTest(day=day):
                self.assertEqual(stage(config,day)["number"], expected)
        self.assertEqual(stage(config,"2027-10-17")["status"],"finished")

    def test_unknown_dates_do_not_guess_and_participation_stops(self):
        data = blank_instance()
        data["config"]["participation"]="participating"
        self.assertEqual(stage(data["config"],"2027-09-20")["status"],"dates_pending")
        data["config"].update(stage_override=3,participation="not_participating")
        self.assertIsNone(stage(data["config"],"2027-09-20")["number"])

    def test_elective_and_balanced_rotation(self):
        self.assertNotIn("商法",subjects("administrative"))
        self.assertNotIn("行政法",subjects("commercial"))
        data=prepared(3,"undecided")
        day1=daily_plan(data,"2027-09-20")["recommended_subjects"]
        self.assertEqual(len(day1),5)
        data["exercises"]=[{"id":s,"subject":s,"completed_on":"2027-09-20"} for s in day1]
        day2=daily_plan(data,"2027-09-21")["recommended_subjects"]
        self.assertEqual(day2[0],"商法")

    def test_cross_day_count_and_no_debt(self):
        data=prepared(2)
        data["exercises"]=[{"id":"q1","subject":"刑法","first_answer_on":"2027-09-13","completed_on":"2027-09-16"}]
        plan=daily_plan(data,"2027-09-16")
        self.assertEqual((plan["case_target"],plan["case_completed"],plan["case_remaining"]),(3,1,2))
        self.assertEqual(plan["historical_target"],1)
        self.assertEqual(daily_plan(data,"2027-09-17")["case_target"],3)

    def test_resume_reserved_subject_and_stage_three_opt_in(self):
        data=prepared(3)
        data["exercises"]=[{"id":"q","subject":"刑法","completed_on":None}]
        plan=daily_plan(data,"2027-09-20")
        self.assertEqual(plan["resume_first"],["q"])
        self.assertNotIn("刑法",plan["recommended_subjects"])
        self.assertEqual(plan["historical_target"],0)
        self.assertEqual(plan["historical_candidates"],[])
        self.assertTrue(plan["historical_optional"])

    def test_fixed_nodes_errors_never_reset_and_d14_repeats(self):
        row=card()
        for day, expected in [("2027-09-03","2027-09-05"),("2027-09-05","2027-09-08"),
                              ("2027-09-08","2027-09-15"),("2027-09-15","2027-09-29")]:
            review_card(row,day,"有提示订正，仍不能证明独立掌握")
            self.assertEqual(next_due(row),expected)
            self.assertEqual(row["status"],"waiting")
        review_card(row,"2027-09-29","独立完整作答",prompted=False,independent_pass=True)
        self.assertEqual(row["status"],"mastered")
        self.assertIsNone(next_due(row))

    def test_no_same_day_multi_node_or_fake_independence(self):
        row=card()
        review_card(row,"2027-09-10","延期后只处理最早节点")
        self.assertEqual(due_cards([row],"2027-09-10",["刑法"]),[])
        self.assertEqual(len(due_cards([row],"2027-09-11",["刑法"])),1)
        with self.assertRaises(ValueError):
            review_card(row,"2027-09-11","提示后作答",prompted=True,independent_pass=True)

    def test_long_theme_not_split_short_themes_combined(self):
        long={"id":"long","text":"字"*1800}
        self.assertEqual(theme_group([long]),[long])
        short=[{"id":"a","text":"字"*400},{"id":"b","text":"字"*600},{"id":"c","text":"字"*900}]
        self.assertEqual(theme_group(short),short[:2])

    def test_pause_and_final_week_never_require_answers(self):
        data=prepared(3)
        data["lifecycle"]="paused"
        self.assertEqual(daily_plan(data,"2027-09-20")["case_target"],0)
        data=prepared(4)
        plan=daily_plan(data,"2027-10-12")
        self.assertEqual(plan["case_target"],0)
        self.assertEqual(plan["historical_target"],0)
        self.assertEqual(plan["resume_first"],[])

    def test_theory_never_blocks_cases_and_corrections_needed_before_continue(self):
        data=prepared(2)
        data["exercises"]=[{"id":s,"subject":s,"completed_on":"2027-09-15"} for s in ("刑法","刑事诉讼法","民事综合")]
        self.assertFalse(daily_plan(data,"2027-09-15")["may_ask_continue"])
        self.assertEqual(daily_plan(prepared(3),"2027-09-20")["case_target"],5)


if __name__ == "__main__":
    unittest.main()
