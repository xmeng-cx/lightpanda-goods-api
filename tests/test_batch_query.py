"""Verify large requests stay within the browser's 800-ID limit."""

import unittest
from unittest.mock import patch

import app


class BatchQueryTest(unittest.TestCase):
    def test_sixteen_hundred_ids_use_two_browser_queries(self):
        ids = [10**12 + value for value in range(1600)]
        calls = []

        def query(chunk, show_id):
            calls.append((chunk, show_id))
            return {
                "showId": "selected-show",
                "requestedCount": len(chunk),
                "successCount": len(chunk) - 1,
                "failureCount": 1,
                "goodsList": [{"goodsId": value, "price": 1234} for value in chunk[:-1]],
                "failures": [{"goodsId": chunk[-1], "reason": "测试失败"}],
                "source": "browser",
            }

        with patch.object(app.browser, "query", side_effect=query):
            result = app.query_goods(ids, None)

        self.assertEqual([len(chunk) for chunk, _ in calls], [800, 800])
        self.assertEqual([show for _, show in calls], [None, "selected-show"])
        self.assertEqual((result["requestedCount"], result["successCount"], result["failureCount"]),
                         (1600, 1598, 2))
        self.assertEqual((len(result["goodsList"]), len(result["failures"]), result["batchCount"]),
                         (1598, 2, 2))
        self.assertEqual(result["source"], "browser")
        self.assertEqual(result["goodsList"][0]["priceYuan"], 12.34)

    def test_eight_hundred_one_ids_and_duplicate(self):
        ids = [10**12 + value for value in range(801)]
        with patch.object(app.browser, "query", side_effect=lambda chunk, show: {
            "showId": "selected-show", "successCount": len(chunk), "failureCount": 0,
            "goodsList": [{"goodsId": value} for value in chunk], "failures": [], "source": "browser",
        }) as query:
            result = app.query_goods(ids + [ids[0]], "selected-show")
        self.assertEqual([len(call.args[0]) for call in query.call_args_list], [800, 1])
        self.assertEqual(result["requestedCount"], 801)
        self.assertEqual(result["batchCount"], 2)
        self.assertEqual(result["source"], "browser")

    def test_browser_query_does_not_use_direct_post(self):
        from browser import Browser

        self.assertFalse(hasattr(Browser, "_direct_query"))


if __name__ == "__main__":
    unittest.main()
