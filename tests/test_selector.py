from collections import Counter
from datetime import timedelta
from unittest.mock import patch
import unittest

from tests import bootstrap  # noqa: F401
from feedback_growth.model import AggregateState, Candidate, PhotographerStats
from feedback_growth.selector import DAILY_TARGET, select_run_candidates
from tests.helpers import dt


NOW = dt()


def photographer(candidate, historical=False):
    return PhotographerStats(
        photographer_id=candidate.photographer_id,
        display_name=candidate.display_name,
        profile_url=candidate.profile_url,
        baseline_work_ids=frozenset(),
        baseline_work_positions={},
        historical_high_potential=historical,
        episodes=(),
        eligible_episodes=(),
        last_comment_at=None,
        today_like_photo_ids=(),
        success_count_30d=2 if candidate.tier == "verified" else 0,
        failure_count=1 if candidate.is_retest else 0,
        dormant_retest_eligible=candidate.is_retest,
    )


def candidate(identifier, tier, page_order, is_retest=False):
    return Candidate(
        photographer_id=identifier,
        display_name=identifier,
        profile_url=f"https://example.test/{identifier}",
        source_photo_id="source",
        source_url="https://example.test/source",
        page_order=page_order,
        tier=tier,
        is_retest=is_retest,
    )


def state_for(candidates):
    return AggregateState(
        photographers={item.photographer_id: photographer(item) for item in candidates},
        known_received_like_pairs=frozenset(),
        daily_tasks={},
        paused_reason=None,
        episodes={},
        outgoing_touches=(),
    )


class SelectorTest(unittest.TestCase):
    def test_complete_day_allocates_200_unique_photographers_without_second_likes(self):
        exploit = [candidate(f"v{i:03}", "verified", i) for i in range(112)]
        retest = [candidate(f"r{i:03}", "new", i, True) for i in range(50)]
        new = [candidate(f"n{i:03}", "new", i) for i in range(38)]
        candidates = exploit + retest + new

        result = select_run_candidates(candidates, state_for(candidates), NOW, seed=8122026, limit=200)

        buckets = Counter(item["bucket"] for item in result.selected)
        ids = [item["photographer_id"] for item in result.selected]
        self.assertEqual(buckets, {"exploit_first": 112, "retest": 50, "new": 38})
        self.assertEqual(len(ids), 200)
        self.assertEqual(len(set(ids)), 200)
        self.assertNotIn("verified_second", buckets)
        self.assertEqual(result.status, "daily_complete")

    def test_retest_new_is_not_new_exploration(self):
        item = candidate("r1", "new", 1, True)

        result = select_run_candidates([item], state_for([item]), NOW, seed=1, limit=1)

        self.assertEqual(result.selected[0]["bucket"], "retest")

    def test_shortage_does_not_weaken_constraints(self):
        candidates = [candidate(f"n{i:02}", "new", i) for i in range(70)]

        result = select_run_candidates(candidates, state_for(candidates), NOW, seed=2, limit=200)

        self.assertEqual(len(result.selected), 70)
        self.assertEqual(result.status, "incomplete_candidate_exhausted")
        self.assertEqual(result.projected_unique_count, 70)

    def test_close_scores_use_page_order(self):
        later = candidate("later", "promising", 2)
        earlier = candidate("earlier", "promising", 1)
        with patch("feedback_growth.selector.random.Random.betavariate", side_effect=[0.70, 0.66]):
            result = select_run_candidates([later, earlier], state_for([later, earlier]), NOW, seed=3, limit=1)

        self.assertEqual(result.selected[0]["photographer_id"], "earlier")

    def test_non_close_scores_use_higher_sample(self):
        later = candidate("later", "promising", 2)
        earlier = candidate("earlier", "promising", 1)
        with patch("feedback_growth.selector.random.Random.betavariate", side_effect=[0.70, 0.64]):
            result = select_run_candidates([later, earlier], state_for([later, earlier]), NOW, seed=3, limit=1)

        self.assertEqual(result.selected[0]["photographer_id"], "later")

    def test_seed_is_repeatable(self):
        candidates = [candidate(f"p{i:02}", "promising", i) for i in range(30)]
        state = state_for(candidates)

        first = select_run_candidates(candidates, state, NOW, seed=8122026, limit=25)
        second = select_run_candidates(candidates, state, NOW, seed=8122026, limit=25)

        self.assertEqual(first, second)

    def test_default_limit_targets_full_day(self):
        candidates = [candidate(f"n{i:03}", "new", i) for i in range(DAILY_TARGET)]

        result = select_run_candidates(candidates, state_for(candidates), NOW, seed=8122026)

        self.assertEqual(len(result.selected), DAILY_TARGET)
        self.assertEqual(result.remaining_daily_quota, 0)
        self.assertEqual(result.status, "daily_complete")
