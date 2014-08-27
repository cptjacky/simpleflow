import unittest

from cdf.core.insights import Insight, InsightValue, InsightTrendPoint
from cdf.query.filter import EqFilter
from cdf.query.aggregation import MaxAggregation


class TestInsight(unittest.TestCase):
    def setUp(self):
        self.identifier = "foo"
        self.name = "Foo"
        self.eq_filter = EqFilter("bar", 5)
        self.max_agg = MaxAggregation("depth")

    def test_query_nominal_case(self):
        insight = Insight(self.identifier,
                          self.name,
                          self.eq_filter,
                          self.max_agg)
        expected_query = {
            "filters": {
                "field": "bar",
                "predicate": "eq",
                "value": 5
            },
            "aggs": [
                {"metrics": [{"max": "depth"}]}
            ]
        }
        self.assertEqual(expected_query, insight.query)

    def test_query_default_aggregation(self):
        insight = Insight(self.identifier,
                          self.name,
                          self.eq_filter)
        expected_query = {
            "filters": {
                "field": "bar",
                "predicate": "eq",
                "value": 5
            },
            "aggs": [
                {"metrics": [{"count": "url"}]}
            ]
        }
        self.assertEqual(expected_query, insight.query)

    def test_query_no_filter(self):
        insight = Insight(self.identifier,
                          self.name,
                          metric_agg=self.max_agg)
        expected_query = {
            "aggs": [
                {"metrics": [{"max": "depth"}]}
            ]
        }
        self.assertEqual(expected_query, insight.query)

    def test_repr(self):
        insight = Insight(self.identifier, self.name)
        self.assertEqual(
            "foo: {'aggs': [{'metrics': [{'count': 'url'}]}]}",
            repr(insight)
        )


class TestInsightValue(unittest.TestCase):
    def setUp(self):
        self.insight = Insight(
            "foo",
            "Foo insight",
            EqFilter("foo_field", 1001)
        )

    def test_to_dict(self):
        trend = [
            InsightTrendPoint(1001, 3.14, "12-08-2014"),
            InsightTrendPoint(2008, 2.72, "11-08-2014")
        ]

        insight_value = InsightValue(self.insight,
                                     "foo_feature",
                                     trend)
        expected_dict = {
            "name": "Foo insight",
            "feature": "foo_feature",
            "query":  {
                'aggs': [{'metrics': [{'count': 'url'}]}],
                'filters': {'field': 'foo_field', 'predicate': 'eq', 'value': 1001}
            },
            "trend": [
                {"crawl_id": 1001, "date_finished": "12-08-2014", "score": 3.14},
                {"crawl_id": 2008, "date_finished": "11-08-2014", "score": 2.72}
            ]
        }
        self.assertEqual(
            expected_dict,
            insight_value.to_dict()
        )


class TestInsightTrendPoint(unittest.TestCase):
    def test_to_dict(self):
        self.assertEqual(
            {"crawl_id": 1001, "date_finished": "12-08-2014", "score": 3.14},
            InsightTrendPoint(1001, 3.14, "12-08-2014").to_dict()
        )