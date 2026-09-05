import unittest

from fastapi.testclient import TestClient

import main


class SkillBridgeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    def test_health_endpoint(self):
        response = self.client.get('/api/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')

    def test_skill_extraction_keeps_java_and_javascript_distinct(self):
        skills = main.extract_skills('Built services using JavaScript and Java.')
        self.assertIn('JavaScript', skills)
        self.assertIn('Java', skills)
        javascript_only = main.extract_skills('Built a JavaScript application.')
        self.assertIn('JavaScript', javascript_only)
        self.assertNotIn('Java', javascript_only)

    def test_learning_path_endpoint(self):
        response = self.client.post('/api/learning-path', json={
            'target_role': 'Data Analyst',
            'current_skills': ['Excel'],
            'weeks_available': 8,
        })
        self.assertEqual(response.status_code, 200)
        self.assertIn('steps', response.json())


if __name__ == '__main__':
    unittest.main()
