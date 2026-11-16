"""Check spreadsheet IDs and batch deletion without touching the live database."""

import importlib.util
import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from openpyxl import load_workbook
from fastapi import HTTPException


APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
spec = importlib.util.spec_from_file_location("goods_web_app", APP_PATH)
goods_app = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = goods_app
spec.loader.exec_module(goods_app)


class BatchExportTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        goods_app.DATA_DIR = Path(self.temp.name)
        goods_app.DB_PATH = goods_app.DATA_DIR / "batches.sqlite3"
        self.api_patch = patch.object(goods_app, "api_call", return_value={"authenticated": True})
        self.api_patch.start()
        self.addCleanup(self.api_patch.stop)
        self.product_id = "1029100000000"
        self.long_title = "测试商品名称" * 14
        result = {
            "requestedGoodsIds": [self.product_id, "1029100000001"],
            "goodsList": [{"goodsId": self.product_id, "title": self.long_title,
                           "price": 59900, "stock": 3, "soldQuantity": 9999,
                           "soldQuantityTip": "已拼21件"}],
            "failures": [{"goodsId": "1029100000001", "reason": "暂未返回"}],
        }
        with goods_app.database() as db:
            for task_type in ("filter", "sales"):
                db.execute("""INSERT INTO batches (created_at, name, task_type, api_base, show_id, total, sold,
                    unsold, sold_out, unknown, sales_threshold, result_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    ("2026-11-14T00:00:00+00:00", "测试批次", task_type, goods_app.API_BASE, "show-1", 2, 1, 0, 0, 1,
                     10, json.dumps(result, ensure_ascii=False)))

    def test_sales_export_keeps_full_product_id_and_summary(self):
        response = goods_app.export_batch(2, x_api_key="test")
        sheet = load_workbook(BytesIO(response.body)).active
        self.assertEqual([sheet.cell(1, col).value for col in range(1, 7)],
                         ["总商品数量", "已出 (销量>10)", "未出 (销量≤10/空)", "售罄", "未知", "出销率"])
        self.assertEqual([sheet.cell(2, col).value for col in range(1, 6)], [2, 1, 0, 0, 1])
        self.assertEqual(sheet["F2"].value, "50.00%")
        self.assertEqual(sheet["B5"].value, self.product_id)
        self.assertEqual(sheet["B5"].data_type, "s")
        self.assertEqual(sheet["B5"].number_format, "@")
        self.assertEqual(sheet["C5"].value, self.long_title)
        self.assertGreaterEqual(sheet.column_dimensions["B"].width, 20)
        self.assertTrue(sheet["C5"].alignment.wrap_text)
        self.assertEqual([sheet.cell(4, col).value for col in range(1, 8)],
                         ["序号", "商品ID", "商品名称", "销量", "价格", "处理状态", "商品链接"])
        self.assertEqual(sheet["D5"].value, 21)
        self.assertEqual(sheet["F5"].value, "已出")
        self.assertEqual(sheet.max_column, 7)
        self.assertEqual(sheet.column_dimensions["C"].width, 45)
        self.assertEqual(sheet["A1"].fill.fgColor.rgb[-6:], "D9EAD3")
        self.assertEqual(sheet["A4"].fill.fgColor.rgb[-6:], "E2EFDA")
        self.assertEqual(sheet["A5"].border.bottom.style, "thin")
        self.assertIsNone(sheet.auto_filter.ref)
        self.assertIsNone(sheet.freeze_panes)
        self.assertRegex(response.headers["content-disposition"], r"filename\*=UTF-8''\d{4}-\d{2}-\d{2}-%E5%87%BA%E9%94%80%E7%8E%8750%25-%E6%88%90%E5%8A%9F1\.xlsx")

    def test_filter_export_and_individual_and_global_delete(self):
        sheet = load_workbook(BytesIO(goods_app.export_batch(1, view="overSales", filterSales=20,
                                                      filterPrice=500, x_api_key="test").body)).active
        self.assertEqual(sheet["B2"].value, self.product_id)
        self.assertEqual(sheet["D2"].value, "已拼21件")
        self.assertEqual(sheet["G2"].value, "超销")
        self.assertEqual(sheet.max_column, 9)
        self.assertIsNone(sheet.auto_filter.ref)
        self.assertIsNone(sheet.freeze_panes)
        self.assertEqual(goods_app.delete_batch(1, x_api_key="test"), {"deleted": 1})
        self.assertEqual(goods_app.clear_batches(x_api_key="test"), {"deleted": 1})
        with goods_app.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM batches").fetchone()[0], 0)

    def test_threshold_change_recalculates_existing_sales_batch(self):
        result = goods_app.update_sales_threshold(goods_app.SalesThresholdBody(salesThreshold=30), x_api_key="test")
        self.assertEqual(result["updated"], 1)
        sales = goods_app.batch_detail(2, x_api_key="test")["batch"]
        self.assertEqual((sales["sold"], sales["unsold"], sales["sales_threshold"]), (0, 1, 30))
        sheet = load_workbook(BytesIO(goods_app.export_batch(2, x_api_key="test").body)).active
        self.assertEqual(sheet["B1"].value, "已出 (销量>30)")
        self.assertEqual(sheet["B2"].value, 0)
        self.assertEqual(sheet["F5"].value, "未出")
        self.assertEqual(goods_app.batch_detail(1, x_api_key="test")["batch"]["sales_threshold"], 10)

    def test_api_address_separates_batch_history(self):
        other_api = "https://other.example:8766"
        self.assertEqual(goods_app.batches("filter", x_api_key="test", x_api_base_url=other_api)["batches"], [])
        with self.assertRaises(HTTPException) as missing:
            goods_app.batch_detail(1, x_api_key="test", x_api_base_url=other_api)
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(goods_app.clear_batches(x_api_key="test", x_api_base_url=other_api), {"deleted": 0})
        self.assertEqual(len(goods_app.batches("filter", x_api_key="test")["batches"]), 1)

    def test_api_address_rejects_paths_and_credentials(self):
        for value in ("file:///tmp/test", "http://user:pass@example.com:8766", "http://example.com:8766/admin"):
            with self.subTest(value=value), self.assertRaises(HTTPException):
                goods_app.api_base_url(value)
        self.assertEqual(goods_app.api_base_url("https://EXAMPLE.COM:8766/"), "https://example.com:8766")
        self.assertEqual(goods_app.api_base_url("http://[::1]:8766/"), "http://[::1]:8766")

    def test_two_thousand_ids_are_saved_as_one_web_batch(self):
        ids = [10**12 + value for value in range(2000)]
        response = {"showId": "show-1", "goodsList": [], "failures": [], "batchCount": 2}
        with patch.object(goods_app, "api_call", return_value=response):
            result = goods_app.query(goods_app.QueryBody(goodsIds=ids, showId="show-1"), x_api_key="test")
        self.assertEqual(result["batchCount"], 2)
        self.assertEqual(len(result["requestedGoodsIds"]), 2000)
        with goods_app.database() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM batches").fetchone()[0], 3)
            row = db.execute("SELECT total, unknown FROM batches WHERE id=?", (result["batchId"],)).fetchone()
        self.assertEqual(tuple(row), (2000, 2000))

    def test_known_goods_never_have_unknown_sales_status(self):
        goods = [
            {"goodsId": "1", "soldQuantityTip": "热销2143件", "stock": 100},
            {"goodsId": "2", "soldQuantityTip": "已拼6件", "stock": 100},
            {"goodsId": "3", "soldQuantityTip": "其他文案", "stock": 100},
        ]
        result = {"requestedGoodsIds": ["1", "2", "3", "4"], "goodsList": goods,
                  "failures": [{"goodsId": "1", "reason": "旧失败项"}, {"goodsId": "4", "reason": "未返回"}]}
        self.assertEqual(goods_app.counts(result, 10),
                         {"total": 4, "sold": 1, "unsold": 2, "sold_out": 0, "unknown": 1})
        self.assertEqual(goods_app.export_status({"goods": goods[0], "failure": result["failures"][0]}, 10), "已出")
        self.assertEqual(goods_app.export_status({"goods": goods[2], "failure": None}, 10), "未出")

    def test_existing_batch_recounts_with_new_sales_rules(self):
        with goods_app.database() as db:
            db.execute("UPDATE batches SET metric_version=2, sold=0, unsold=0, unknown=2 WHERE id=2")
        with goods_app.database() as db:
            row = db.execute("SELECT sold, unsold, unknown, metric_version FROM batches WHERE id=2").fetchone()
        self.assertEqual(tuple(row), (1, 0, 1, 3))



if __name__ == "__main__":
    unittest.main()
