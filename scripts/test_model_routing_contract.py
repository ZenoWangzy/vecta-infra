"""Every caller-facing model name routes to the GLM coding plan; paid DeepSeek flash is only the fallback."""
import unittest
from pathlib import Path
import yaml


class ModelRoutingContract(unittest.TestCase):
    def test_mypc_uses_an_immutable_image_and_effective_drop_params(self):
        root = Path(__file__).resolve().parents[1]
        inventory = yaml.safe_load((root / 'inventories/mypc/group_vars/mypc.yml').read_text())
        self.assertRegex(inventory['litellm_image'], r'/berriai/litellm@sha256:[a-f0-9]{64}$')
        self.assertEqual(inventory['litellm_published_ports'], ['127.0.0.1:4000:4000'])
        config = yaml.safe_load((root / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text())
        self.assertIs(config['litellm_settings']['drop_params'], True)
        self.assertNotIn('drop_params', config.get('general_settings', {}))

    def test_every_alias_uses_glm_first_and_deepseek_flash_only_as_fallback(self):
        # 2026-10-09 founder: GLM coding plan first, paid DeepSeek flash once GLM is used up.
        # Cooldowns stay off: the old 30 s cooldown had billed every call in a 429 burst.
        root = Path(__file__).resolve().parents[1]
        text = (root / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text()
        config = yaml.safe_load(text)
        models = {item['model_name']: item['litellm_params'] for item in config['model_list']}
        for alias in ['glm-5.3-flash', 'glm-5', 'glm-5.1', 'deepseek-chat', 'deepseek-v4-flash-vision-exp', 'multimodal-vision', 'deepseek-flash']:
            self.assertEqual(models[alias]['model'], 'openai/glm-5.3-flash')
            self.assertEqual(models[alias]['api_base'], 'https://open.bigmodel.cn/api/coding/paas/v4')
            self.assertEqual(models[alias]['api_key'], 'os.environ/ZAI_API_KEY')
        self.assertEqual(models['dpsk-flash-fallback']['api_base'], 'https://api.deepseek.com')
        self.assertEqual(models['dpsk-flash-fallback']['api_key'], 'os.environ/DEEPSEEK_API_KEY')
        self.assertEqual(text.count('api.deepseek.com'), 1)
        router = config['router_settings']
        callers = [name for name in models if name != 'dpsk-flash-fallback']
        self.assertEqual(router['fallbacks'], [{name: ['dpsk-flash-fallback']} for name in callers])
        self.assertIs(router['disable_cooldowns'], True)
        self.assertEqual(router['retry_policy'], {'RateLimitErrorRetries': 5})
        self.assertEqual(router['num_retries'], 1)

    def test_glm_aliases_think_at_high_by_default(self):
        # #1937 founder ruling: thinking high by default, not max, and not disabled (#1923 was a stopgap).
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text())
        models = {item['model_name']: item['litellm_params'] for item in config['model_list']}
        for alias in ['glm-5.3-flash', 'glm-5', 'glm-5.1', 'deepseek-chat', 'deepseek-v4-flash-vision-exp', 'multimodal-vision', 'deepseek-flash', 'dpsk-flash-fallback']:
            self.assertEqual(models[alias]['extra_body'], {'thinking': {'type': 'enabled'}, 'reasoning_effort': 'high'})

    def test_silence_is_bounded_separately_from_the_total_budget(self):
        # 2026-09-29: timeout 600 alone let a hung GLM stream hold a turn for 21 minutes.
        config = yaml.safe_load((Path(__file__).resolve().parents[1] / 'roles/infra-bootstrap/templates/litellm-config.yaml.j2').read_text())
        router = config['router_settings']
        self.assertEqual(router['timeout'], 600)
        self.assertLessEqual(router['stream_timeout'], 120)
        self.assertLess(router['stream_timeout'], router['timeout'])

if __name__ == '__main__':
    unittest.main()
