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


if __name__ == '__main__':
    unittest.main()
