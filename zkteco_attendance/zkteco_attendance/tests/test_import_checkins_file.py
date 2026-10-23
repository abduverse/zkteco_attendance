"""
Unit tests for the Import Checkins page upload parser.

The page hands the picked file to parse_checkins_file() base64-encoded and
gets back ready-to-import {user_id, timestamp, log_type} rows. These tests
cover the formats people actually upload: an Excel workbook (the default
"Save As" in Excel), a CSV exported by Excel for each locale, and files
that carry title rows above the header.

Run with: bench run-tests --app zkteco_attendance
"""

import base64
import io
import unittest
from datetime import date, datetime, time

from openpyxl import Workbook

from zkteco_attendance.zkteco_attendance.page.zk_import_checkins.zk_import_checkins import (
    parse_checkins_file,
)


def _xlsx(rows):
    workbook = Workbook()
    sheet = workbook.active
    for row in rows:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _parse(filename, raw):
    return parse_checkins_file(
        filename=filename, content=base64.b64encode(raw).decode("utf-8")
    )


class TestExcelWorkbook(unittest.TestCase):

    def test_xlsx_with_expected_headers(self):
        result = _parse("checkins.xlsx", _xlsx([
            ["user_id", "time", "log_type"],
            ["1001", datetime(2026, 2, 1, 8, 2, 11), "IN"],
            ["1001", datetime(2026, 2, 1, 17, 31, 45), "OUT"],
        ]))
        self.assertTrue(result["success"])
        self.assertEqual(result["total_rows"], 2)
        self.assertEqual(result["rows"][0], {
            "user_id": "1001",
            "timestamp": "2026-02-01 08:02:11",
            "log_type": "IN",
        })
        self.assertEqual(result["rows"][1]["log_type"], "OUT")

    def test_xlsx_with_excel_style_headers_and_title_row(self):
        result = _parse("checkins.xlsx", _xlsx([
            ["Attendance export — Branch 2"],
            [],
            ["Employee ID", "Punch Time", "Log Type"],
            [1002, datetime(2026, 2, 1, 8, 15, 3), None],
        ]))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"], [{
            "user_id": "1002",
            "timestamp": "2026-02-01 08:15:03",
            "log_type": "",
        }])

    def test_xlsx_without_log_type_column(self):
        result = _parse("checkins.xlsx", _xlsx([
            ["user_id", "time"],
            ["1001", datetime(2026, 2, 1, 8, 2, 11)],
        ]))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["log_type"], "")

    def test_legacy_xls_is_rejected_with_guidance(self):
        result = _parse("checkins.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
        self.assertFalse(result["success"])
        self.assertIn(".xlsx", result["error"])

    def test_corrupt_xlsx_reports_a_readable_error(self):
        result = _parse("checkins.xlsx", b"PK\x03\x04not-a-workbook")
        self.assertFalse(result["success"])
        self.assertIn("could not be opened", result["error"])


class TestSeparateDateAndTimeColumns(unittest.TestCase):

    def test_xlsx_with_date_and_time_columns(self):
        result = _parse("checkins.xlsx", _xlsx([
            ["Employee ID", "Date", "Time", "In/Out"],
            [1001, date(2026, 2, 1), time(8, 2, 11), "IN"],
            [1001, date(2026, 2, 1), time(17, 31, 45), "OUT"],
        ]))
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"], [
            {"user_id": "1001", "timestamp": "2026-02-01 08:02:11", "log_type": "IN"},
            {"user_id": "1001", "timestamp": "2026-02-01 17:31:45", "log_type": "OUT"},
        ])

    def test_csv_with_date_and_time_columns(self):
        raw = b"user_id,date,time,log_type\r\n1001,2026-02-01,08:02:11,IN\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["timestamp"], "2026-02-01 08:02:11")

    def test_day_first_date_column(self):
        raw = b"user_id,date,time\r\n1001,01/02/2026,08:02:11\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["timestamp"], "2026-02-01 08:02:11")

    def test_month_first_date_column(self):
        raw = b"user_id,date,time\r\n1001,10/22/2026,16:05:00\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["timestamp"], "2026-10-22 16:05:00")

    def test_time_column_with_am_pm(self):
        raw = b"user_id,date,time\r\n1001,2026-02-01,04:05:00 PM\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["timestamp"], "2026-02-01 16:05:00")

    def test_excel_serials_in_both_columns(self):
        raw = b"user_id,date,time\r\n1001,46054,0.335\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["timestamp"], "2026-02-01 08:02:24")

    def test_missing_time_cell_uses_start_of_day(self):
        raw = b"user_id,date,time\r\n1001,2026-02-01,\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["timestamp"], "2026-02-01 00:00:00")

    def test_date_column_without_time_column_is_an_error(self):
        result = _parse("checkins.csv", b"user_id,date\r\n1001,2026-02-01\r\n")
        self.assertFalse(result["success"])
        self.assertIn("time column", result["error"])


class TestDelimitedText(unittest.TestCase):

    def test_comma_csv(self):
        raw = b"user_id,time,log_type\r\n1001,2026-02-01 08:02:11,IN\r\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"], [{
            "user_id": "1001",
            "timestamp": "2026-02-01 08:02:11",
            "log_type": "IN",
        }])

    def test_semicolon_csv_with_bom(self):
        # Excel writes this for locales where "," is the decimal separator
        raw = "\ufeffuser_id;time;log_type\r\n1001;2026-02-01 08:02:11;IN\r\n".encode("utf-8")
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["user_id"], "1001")
        self.assertEqual(result["rows"][0]["timestamp"], "2026-02-01 08:02:11")

    def test_tab_separated_text(self):
        raw = b"user_id\ttime\tlog_type\n1001\t2026-02-01 08:02:11\tOUT\n"
        result = _parse("checkins.txt", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["rows"][0]["log_type"], "OUT")

    def test_title_rows_above_the_header(self):
        raw = (
            b"Attendance export - Branch 2\r\n"
            b"\r\n"
            b"user_id,time,log_type\r\n"
            b"1001,2026-02-01 08:02:11,IN\r\n"
        )
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertEqual(result["total_rows"], 1)

    def test_unformatted_excel_date_serial(self):
        raw = b"user_id,time\n1001,46054.335\n"
        result = _parse("checkins.csv", raw)
        self.assertTrue(result["success"], result)
        self.assertTrue(result["rows"][0]["timestamp"].startswith("2026-02-01 08:02"))


class TestBadInput(unittest.TestCase):

    def test_file_without_required_columns_reports_first_row(self):
        result = _parse("checkins.csv", b"Name,Department\r\nAli,HR\r\n")
        self.assertFalse(result["success"])
        self.assertIn("user_id", result["error"])
        self.assertEqual(result["header"], ["Name", "Department"])

    def test_header_only_file_has_no_data_rows(self):
        result = _parse("checkins.csv", b"user_id,time,log_type\r\n")
        self.assertFalse(result["success"])
        self.assertIn("No data rows", result["error"])

    def test_empty_file(self):
        result = _parse("checkins.csv", b"")
        self.assertFalse(result["success"])
        self.assertIn("empty", result["error"])

    def test_missing_content(self):
        result = parse_checkins_file(filename="checkins.csv", content=None)
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
