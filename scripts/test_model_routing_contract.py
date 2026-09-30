"""Production text and vision routes share a one-way GLM -> DeepSeek chain."""
import unittest
from pathlib import Path
import yaml


class ModelRoutingContract(unittest.TestCase):
    def test_subscription_first_and_paid_terminal(self):
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text())
        models = {item['model_name']: item['litellm_params'] for item in config['model_list']}
        fallbacks = {key: value for item in config['router_settings']['fallbacks'] for key, value in item.items()}
        for alias in ['glm-5.3-flash', 'glm-5', 'glm-5.1', 'deepseek-chat', 'deepseek-v4-flash-vision-exp', 'multimodal-vision']:
            self.assertEqual(models[alias]['model'], 'openai/glm-5.3-flash')
            self.assertEqual(models[alias]['api_base'], 'https://open.bigmodel.cn/api/coding/paas/v4')
            self.assertEqual(models[alias]['api_key'], 'os.environ/ZAI_API_KEY')
            self.assertEqual(fallbacks[alias], ['deepseek-flash'])
        self.assertEqual(models['deepseek-flash']['model'], 'openai/deepseek-flash')
        self.assertEqual(models['deepseek-flash']['api_key'], 'os.environ/DEEPSEEK_API_KEY')
        self.assertNotIn('deepseek-flash', fallbacks)
        self.assertEqual(config['router_settings']['num_retries'], 1)

    def test_employee_aliases_think_at_low_by_default(self):
        # issue 2152 supersedes the effort default; thinking remains enabled.
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text())
        models = {item['model_name']: item['litellm_params'] for item in config['model_list']}
        for alias in ['glm-5.3-flash', 'glm-5', 'glm-5.1', 'deepseek-chat', 'deepseek-v4-flash-vision-exp', 'multimodal-vision']:
            self.assertEqual(models[alias]['extra_body'], {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'})
        self.assertEqual(models['deepseek-flash']['extra_body'], {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'low'})

    def test_silence_is_bounded_separately_from_the_total_budget(self):
        # 2026-09-29: timeout 600 alone let a hung GLM stream hold a turn for 21 minutes.
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text())
        router = config['router_settings']
        self.assertEqual(router['timeout'], 600)
        self.assertLessEqual(router['stream_timeout'], 120)
        self.assertLess(router['stream_timeout'], router['timeout'])

if __name__ == '__main__':
    unittest.main()
