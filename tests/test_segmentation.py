import unittest

from app.segmentation import preview_segments


class SegmentationTests(unittest.TestCase):
    def test_groups_non_adjacent_rows_and_reports_unassigned(self):
        result = preview_segments(b'AGENT_NAME,FIRSTNAME\nAma,A\nKojo,B\n Ama ,C\n,D\n')
        self.assertEqual(result['segments'], [{'agent': 'Ama', 'count': 2}, {'agent': 'Kojo', 'count': 1}])
        self.assertEqual(result['unassigned'], 1)
        self.assertEqual(result['total'], 4)

    def test_bom_quoted_names_and_multiline_values(self):
        result = preview_segments('\ufeffOwner,COMMENT_DATE\n"Mensah, Ama","line one\nline two"\n'.encode(), 'Owner')
        self.assertEqual(result['segments'], [{'agent': 'Mensah, Ama', 'count': 1}])

    def test_unknown_column_can_be_selected(self):
        contents = b'Owner,FIRSTNAME\nAma,A\n'
        self.assertIsNone(preview_segments(contents)['agent_column'])
        self.assertEqual(preview_segments(contents, 'Owner')['segments'][0]['agent'], 'Ama')

    def test_rejects_invalid_files(self):
        for contents in [b'', b'AGENT,AGENT\nA,A\n', b'AGENT\n', b'AGENT,X\nA\n',
                         b'AGENT\n"Unclosed', b'AGENT\n\xff\n']:
            with self.subTest(contents=contents), self.assertRaises(ValueError):
                preview_segments(contents)

    def test_missing_selected_column_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'Select an agent column'):
            preview_segments(b'AGENT\nAma\n', 'Owner')

    def test_similar_names_are_not_silently_merged(self):
        result = preview_segments(b'AGENT\nAma\nama\n')
        self.assertEqual(len(result['segments']), 2)
