"""Verify large requests stay within the browser's 1000-ID limit."""

import unittest
from unittest.mock import patch

import app


class BatchQueryTest(unittest.TestCase):
    def test_two_thousand_ids_use_two_browser_queries(self):
        ids = [10**12 + value for value in range(2000)]
        calls = []

        def query(chunk, show_id):
            calls.append((chunk, show_id))
            source = "direct" if len(calls) == 1 else "browser"
            return {
                "showId": "selected-show",
                "requestedCount": len(chunk),
                "successCount": len(chunk) - 1,
                "failureCount": 1,
                "goodsList": [{"goodsId": value, "price": 1234} for value in chunk[:-1]],
                "failures": [{"goodsId": chunk[-1], "reason": "测试失败"}],
                "source": source,
            }

        with patch.object(app.browser, "query", side_effect=query):
            result = app.query_goods(ids, None)

        self.assertEqual([len(chunk) for chunk, _ in calls], [1000, 1000])
        self.assertEqual([show for _, show in calls], [None, "selected-show"])
        self.assertEqual((result["requestedCount"], result["successCount"], result["failureCount"]),
                         (2000, 1998, 2))
        self.assertEqual((len(result["goodsList"]), len(result["failures"]), result["batchCount"]),
                         (1998, 2, 2))
        self.assertEqual(result["source"], "mixed")
        self.assertEqual(result["goodsList"][0]["priceYuan"], 12.34)

    def test_one_thousand_one_ids_and_duplicate(self):
        ids = [10**12 + value for value in range(1001)]
        with patch.object(app.browser, "query", side_effect=lambda chunk, show: {
            "showId": "selected-show", "successCount": len(chunk), "failureCount": 0,
            "goodsList": [{"goodsId": value} for value in chunk], "failures": [], "source": "direct",
        }) as query:
            result = app.query_goods(ids + [ids[0]], "selected-show")
        self.assertEqual([len(call.args[0]) for call in query.call_args_list], [1000, 1])
        self.assertEqual(result["requestedCount"], 1001)
        self.assertEqual(result["batchCount"], 2)
        self.assertEqual(result["source"], "direct")


if __name__ == "__main__":
    unittest.main()
