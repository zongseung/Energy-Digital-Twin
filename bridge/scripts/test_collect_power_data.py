import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

import collect_power_data as collector
from collect_power_data import archive_csvs, csv_summary, routes


class CollectionChecks(unittest.TestCase):
    def generation(self, change=None):
        rows = [['2025-01-01', str(hour), fuel, '1.25']
                for hour in range(1, 25) for fuel in ('LNG', '신재생(풍력)')]
        if change:
            change(rows)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['거래일자', '거래시간', '연료원', '전력거래량(MWh)'])
        writer.writerows(rows)
        return stream.getvalue().encode('utf-8-sig')

    def test_generation_rejects_duplicate_missing_and_nonfinite_observations(self):
        self.assertEqual(csv_summary(self.generation(), 'generation')['row_count'], 48)
        for change in (lambda r: r.append(r[0]), lambda r: r.pop(),
                       lambda r: r[0].__setitem__(3, 'NaN')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                csv_summary(self.generation(change), 'generation')
        with self.assertRaises(ValueError):
            csv_summary(self.generation().replace(b'(MWh)', b'(MW)'), 'generation')
        with self.assertRaises(ValueError):
            csv_summary(b'<html>download requires authentication</html>', 'generation')

    def test_negative_source_settlement_values_are_flagged_and_preserved(self):
        raw = self.generation(lambda rows: rows[0].__setitem__(3, '-0.492436'))
        self.assertEqual(csv_summary(raw, 'generation')['negative_energy_count'], 1)

    def test_historical_routes_preserve_separate_numbered_circuits(self):
        text = '| 1 | A—B#1 | 200 | 2 | A—B#1 | 200 |\n'
        self.assertEqual([r['number'] for r in routes(text)], [1, 2])
        self.assertEqual([r['name'] for r in routes(text)], ['A—B#1', 'A—B#1'])
        with self.assertRaises(ValueError):
            routes('| 1 | A—B | 200 | 1 | C—D | 200 |\n')
        with self.assertRaises(ValueError):
            routes('| 1 | A—B | NaN |\n')

    def test_archive_selects_jeju_members_and_cached_data_requires_its_hash(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('제주 태양광.csv', b'pv')
            archive.writestr('제주 풍력.csv', b'wind')
            archive.writestr('육지 풍력.csv', b'mainland')
        self.assertEqual([data for _, data in archive_csvs(stream.getvalue())], [b'pv', b'wind'])
        old_root = collector.ROOT
        with tempfile.TemporaryDirectory() as directory:
            collector.ROOT = Path(directory)
            try:
                metadata = collector.publish('sample.jsonl', b'{}\n', {'source': 'public-test'})
                self.assertEqual(collector.reused('sample.jsonl', 'public-test'), metadata)
                (collector.ROOT / 'sample.jsonl').write_bytes(b'corrupt data')
                with self.assertRaises(ValueError):
                    collector.reused('sample.jsonl', 'public-test')
            finally:
                collector.ROOT = old_root

    def test_route_resume_keeps_the_original_source_in_the_manifest(self):
        old_root = collector.ROOT
        with tempfile.TemporaryDirectory() as directory:
            collector.ROOT = Path(directory)
            try:
                path = collector.ROOT / 'input.md'
                path.write_text('\n'.join(f'| {n} | A—B#{n} | 200 |' for n in range(1, 40)))
                self.assertEqual(len(collector.collect_routes(path)), 2)
                self.assertEqual(len(collector.collect_routes(path)), 2)
            finally:
                collector.ROOT = old_root


if __name__ == '__main__':
    unittest.main()
