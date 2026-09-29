import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import collect_power_data as files
import collect_registered_api as api


def response(rows, total=None, page=1, size=2):
    return json.dumps({'response': {'header': {'resultCode': '00'}, 'body': {
        'pageNo': page, 'numOfRows': size, 'totalCount': len(rows) if total is None else total,
        'items': {'item': rows}}}}).encode()


class RegisteredApiChecks(unittest.TestCase):
    def test_key_accepts_spacing_quotes_and_one_url_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / '.env'
            path.write_text('# comment\napi_key = "test%2Bvalue%2F%3D"\n')
            self.assertEqual(api.read_key(path), 'test+value/=')
            path.write_text('api_key = ""\n')
            with self.assertRaisesRegex(ValueError, 'api_key_missing'):
                api.read_key(path)

    def test_page_rejects_gateway_errors_truncation_and_wrong_offsets(self):
        self.assertEqual(api.parse_page(response([{'id': 1}]), 1, 2), ([{'id': 1}], 1))
        self.assertEqual(api.parse_page(response([], total=0), 1, 2), ([], 0))
        for raw in (b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>',
                    response([{'id': 1}], total=3), response([], page=2), b'<html>error</html>'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                api.parse_page(raw, 1, 2)

    def test_credentials_cannot_be_forwarded_or_persisted(self):
        with self.assertRaisesRegex(ValueError, 'redirect_blocked'):
            api.NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.org')
        for raw in (b'test+value/=', b'test%2Bvalue%2F%3D'):
            with self.assertRaisesRegex(ValueError, 'credential_echo'):
                api.reject_key_echo(raw, 'test+value/=')

    def test_pagination_preserves_missing_height_and_resume_checks_identity(self):
        rows = [{'sigunguCd': '50130', 'bjdongCd': '25033', 'mgmBldrgstPk': n, 'heit': h}
                for n, h in ((1, 3), (2, 0), (3, ''))]
        params = {'sigunguCd': '50130', 'bjdongCd': '25033', 'numOfRows': 2}
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)):
            with patch.object(api, 'fetch', side_effect=[response(rows[:2], total=3), response(rows[2:], total=3, page=2)]) as call:
                meta = api.collect_series('buildings', 'sample', params, 'test-key')
                self.assertEqual(call.call_count, 2)
                self.assertEqual((meta['row_count'], meta['positive_height_count'], meta['missing_height_count']), (3, 1, 2))
            with patch.object(api, 'fetch', side_effect=AssertionError('cache should prevent requests')):
                self.assertEqual(api.collect_series('buildings', 'sample', params, 'test-key')['sha256'], meta['sha256'])
            page = Path(directory) / 'pages/sample/00002.json'
            page.write_bytes(b'corrupt')
            with self.assertRaises(ValueError):
                api.collect_series('buildings', 'sample', params, 'test-key')

    def test_series_rejects_duplicate_foreign_and_shifted_pages(self):
        row = {'sigunguCd': '50130', 'bjdongCd': '25033', 'mgmBldrgstPk': 1, 'heit': 3}
        params = {'sigunguCd': '50130', 'bjdongCd': '25033', 'numOfRows': 1}
        for second in (row, dict(row, sigunguCd='11110', mgmBldrgstPk=2), dict(row, mgmBldrgstPk=None)):
            with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)), patch.object(api, 'fetch', side_effect=[response([row], 2, 1, 1), response([second], 2, 2, 1)]):
                with self.assertRaises(ValueError):
                    api.collect_series('buildings', 'sample', params, 'test-key')
                self.assertFalse((Path(directory) / 'sample.jsonl.metadata.json').exists())
                self.assertFalse((Path(directory) / 'pages/sample/00002.json.metadata.json').exists())
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)), patch.object(api, 'fetch', side_effect=[response([row], 2, 1, 1), response([dict(row, mgmBldrgstPk=2)], 3, 2, 1)]):
            with self.assertRaisesRegex(ValueError, 'total_changed'):
                api.collect_series('buildings', 'sample', params, 'test-key')

    def test_partial_resume_revalidates_cached_source_before_mixing_new_pages(self):
        params = {'sigunguCd': '50130', 'bjdongCd': '25033', 'numOfRows': 2}
        rows = [dict(sigunguCd='50130', bjdongCd='25033', mgmBldrgstPk=n, heit=3) for n in range(1, 6)]
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)):
            files.publish('pages/sample/00001.json', response(rows[:2], 4),
                          {'source': {'endpoint': api.ENDPOINTS['buildings'], 'params': dict(params, pageNo=1)}}, root=api.ROOT)
            def shifted_source(kind, query, key):
                return response([rows[0], rows[2]] if query['pageNo'] == 1 else rows[3:], 4, query['pageNo'])
            with patch.object(api, 'fetch', side_effect=shifted_source), self.assertRaisesRegex(ValueError, 'checkpoint_source_changed'):
                api.collect_series('buildings', 'sample', params, 'test-key')

    def test_check_rejects_manifest_that_omits_its_declared_jobs(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)):
            api.write_manifest({'complete': True, 'datasets': [], 'expected_datasets': 5,
                'scope': {'asos_year': 2025, 'stations': {str(k): v for k, v in api.STATIONS.items()}, 'building_legal_codes': ['5013025033']}})
            with self.assertRaises(ValueError):
                asyncio.run(api.main(SimpleNamespace(check=True)))

    def test_check_verifies_manifest_hash_before_accepting_a_reduced_scope(self):
        codes = ['5013025033', '5013025034']
        items = {label: {'file': label + '.jsonl', 'source': {'endpoint': api.ENDPOINTS[kind], 'params': params}}
                 for kind, label, params in api.jobs_for(2025, codes)}
        manifest = {'complete': True, 'expected_datasets': 6, 'datasets': list(items.values()),
                    'scope': {'asos_year': 2025, 'stations': {str(k): v for k, v in api.STATIONS.items()}, 'building_legal_codes': codes}}
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)), patch.object(api, 'collect_series', side_effect=lambda kind, label, *args, **kw: items[label]):
            api.write_manifest(manifest)
            self.assertEqual(asyncio.run(api.main(SimpleNamespace(check=True))), 0)
            manifest['scope']['building_legal_codes'].pop()
            manifest['datasets'].pop()
            manifest['expected_datasets'] -= 1
            (api.ROOT / 'manifest.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'existing_dataset_verification_failed'):
                asyncio.run(api.main(SimpleNamespace(check=True)))

    def test_weather_keeps_zero_missing_qc_and_rejects_wrong_station(self):
        params = {'stnIds': 184, 'startDt': '20250101', 'endDt': '20251231', 'numOfRows': 2}
        rows = [dict(stnId='184', stnNm='제주', tm='2025-01-01 00:00', ws='0', wd='0', wsQcflg='0', wdQcflg='0'),
                dict(stnId='184', stnNm='제주', tm='2025-01-01 01:00', ws='', wd='', wsQcflg='9', wdQcflg='9')]
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)), patch.object(api, 'fetch', return_value=response(rows)):
            meta = api.collect_series('asos', 'sample', params, 'test-key')
            self.assertEqual((meta['missing_ws_count'], meta['missing_hour_count']), (1, 8758))
        rows[1]['stnId'] = '185'
        with tempfile.TemporaryDirectory() as directory, patch.object(api, 'ROOT', Path(directory)), patch.object(api, 'fetch', return_value=response(rows)):
            with self.assertRaises(ValueError):
                api.collect_series('asos', 'sample', params, 'test-key')


if __name__ == '__main__':
    unittest.main()
