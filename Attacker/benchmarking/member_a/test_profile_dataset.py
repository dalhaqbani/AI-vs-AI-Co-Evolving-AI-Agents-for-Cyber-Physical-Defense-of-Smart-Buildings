import io
import unittest

from profile_dataset import combine, profile_csv


class ProfileTests(unittest.TestCase):
    def test_column_order_does_not_change_missing_counts(self):
        first = profile_csv(io.StringIO("a,b,label\n,2,Benign\n"), "one.csv", "label", {"benign"})
        second = profile_csv(io.StringIO("b,label,a\n,Attack,1\n"), "two.csv", "label", {"benign"})
        result = combine([first, second], "example", "label", {"benign"})
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["column_count"], 3)
        self.assertEqual(result["column_order_variants"], 2)
        self.assertEqual(result["missing_by_column"], {"a": 1, "b": 1, "label": 0})

    def test_unlabelled_packet_rows_are_not_assumed_benign(self):
        source = io.StringIO("packetLength,RSSI\n21,-\n18,-65\n")
        result = profile_csv(source, "packets.csv", None, {"benign"},
                             missing_tokens=["", "-"])
        self.assertEqual(result["rows"], 2)
        self.assertEqual(result["missing_cells"], 1)
        self.assertFalse(result["classification_available"])
        self.assertEqual(result["unlabelled_rows"], 2)
        self.assertEqual(result["benign_rows"], 0)
        self.assertEqual(result["attack_rows"], 0)
        self.assertIsNone(result["benign_to_attack_ratio"])

    def test_two_benign_labels_and_numeric_inference(self):
        text = "value,label\n1,Benign\n2,Benign-with-mqtt\nnan,Flood\n"
        first = profile_csv(io.StringIO(text), "ip.csv", "label", {"benign", "benign-with-mqtt"})
        second = profile_csv(io.StringIO(text), "ip.csv", "label", {"benign", "benign-with-mqtt"},
                             infer_types=True)
        self.assertEqual(first, second)
        self.assertEqual(first["benign_rows"], 2)
        self.assertEqual(first["attack_rows"], 1)
        self.assertEqual(first["benign_to_attack_ratio"], 2)

    def test_all_benign_has_no_defined_ratio(self):
        result = profile_csv(io.StringIO("value,label\n1,Benign\n"), "flows.csv", "label", {"benign"})
        self.assertEqual(result["benign_rows"], 1)
        self.assertEqual(result["attack_rows"], 0)
        self.assertIsNone(result["benign_to_attack_ratio"])

    def test_missing_labels_and_unknown_are_separate(self):
        source = io.StringIO("value,label\n1,Benign\n,Attack\nNA,Unknown\n2,\n3,   \n")
        result = profile_csv(source, "example.csv", "label", {"benign"})
        self.assertEqual(result["rows"], 5)
        self.assertEqual(result["column_count"], 2)
        self.assertEqual(result["missing_cells"], 3)
        self.assertEqual(result["benign_rows"], 1)
        self.assertEqual(result["attack_rows"], 1)
        self.assertEqual(result["unknown_rows"], 1)
        self.assertEqual(result["unlabelled_rows"], 2)
        total = combine([result, result], "example", "label", {"benign"})
        self.assertEqual(total["row_count"], 10)
        self.assertEqual(total["benign_to_attack_ratio"], 1)
        self.assertEqual(total["unknown_rows"], 2)

    def test_different_schemas_are_not_combined(self):
        first = profile_csv(io.StringIO("a,label\n1,Benign\n"), "a.csv", "label", {"benign"})
        second = profile_csv(io.StringIO("b,label\n1,Attack\n"), "b.csv", "label", {"benign"})
        with self.assertRaises(ValueError):
            combine([first, second], "example", "label", {"benign"})

    def test_missing_label_column(self):
        with self.assertRaises(ValueError):
            profile_csv(io.StringIO("value,category\n1,Benign\n"), "example.csv", "label", {"benign"})


if __name__ == "__main__":
    unittest.main()
