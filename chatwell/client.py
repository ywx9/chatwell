import ipaddress
import json
import math
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


def reject_constant(value):
    raise ValueError(f'Invalid JSON constant: {value}')


class LlamaClient:
    def __init__(self, endpoint='http://127.0.0.1:8080', timeout=180, debug_dir=None, *, inference_options=None, default_max_tokens=6144, api_key=None, model=None, context_size=16384):
        parsed = urlsplit(endpoint)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment):
            raise ValueError('Endpoint must be an HTTP(S) URL without credentials, query or fragment')
        try:
            parsed.port
        except ValueError:
            raise ValueError('Endpoint has an invalid port') from None
        if api_key and any(c in api_key for c in '\r\n'):
            raise ValueError('Invalid API key format')
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError('Timeout must be positive and finite')
        self.url = endpoint.rstrip('/')
        if not self.url.endswith('/v1'):
            self.url += '/v1'
        self.timeout = timeout
        self.debug_dir = Path(debug_dir) if debug_dir else None
        self.inference_options = dict(inference_options or {})
        if isinstance(context_size, bool) or not isinstance(context_size, int) or context_size < 1:
            raise ValueError('context_size must be a positive integer')
        self.context_size = context_size
        self.default_max_tokens = default_max_tokens
        self.api_key = api_key
        self.model = model
        self.count = 0
        try:
            loopback = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = False
        # Keep localhost direct; remote servers may require the user's proxy configuration.
        proxy = urllib.request.ProxyHandler({}) if loopback else urllib.request.ProxyHandler()
        self.opener = urllib.request.build_opener(proxy, NoRedirect())

    def probe_metadata(self):
        """Optional llama.cpp /props; absence must not block compatible servers."""
        provenance = dict(getattr(self, 'provenance', {'management': 'external', 'llama_cpp_version': None,
                          'model_file': None, 'model_sha256': None, 'source': 'unavailable'}))
        base = self.url[:-3] if self.url.endswith('/v1') else self.url
        request = urllib.request.Request(base + '/props')
        if self.api_key:
            request.add_header('Authorization', 'Bearer ' + self.api_key)
        try:
            with self.opener.open(request, timeout=min(self.timeout, 5)) as response:
                props = json.loads(response.read().decode('utf-8'), parse_constant=reject_constant)
            version = props.get('build_info') if isinstance(props, dict) else None
            if isinstance(version, str):
                provenance.update(llama_cpp_version=version, source='server_props')
        except (OSError, ValueError, TypeError):
            pass
        return provenance

    def complete(self, messages, options, schema=None):
        payload = dict(options, **self.inference_options)
        payload.setdefault('max_tokens', self.default_max_tokens)
        payload.update(messages=messages, stream=False)
        if schema is not None:
            payload['response_format'] = {'type': 'json_schema', 'json_schema': {'name': 'chatwell', 'strict': True, 'schema': schema}}
        if self.model:
            payload['model'] = self.model
        body = json.dumps(payload, allow_nan=False).encode('utf-8')
        request = urllib.request.Request(self.url + '/chat/completions', body, {'Content-Type': 'application/json'})
        if self.api_key:
            request.add_header('Authorization', 'Bearer ' + self.api_key)
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read().decode('utf-8')
        if self.debug_dir:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
            self.count += 1
            (self.debug_dir / f'response-{self.count:03d}.json').write_text(raw, encoding='utf-8')
        result = json.loads(raw, parse_constant=reject_constant)
        try:
            choice = result['choices'][0]
            content = choice['message']['content']
            finish_reason = choice.get('finish_reason')
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ValueError('Invalid chat/completions response: missing choice/message/content') from None
        usage = result.get('usage') or {}
        self.last_metrics = {key: usage.get(key) if isinstance(usage, dict) else None
                             for key in ('prompt_tokens', 'completion_tokens')}
        self.last_metrics['finish_reason'] = finish_reason
        self.response_model = result.get('model') if isinstance(result.get('model'), str) else None
        if finish_reason == 'length':
            raise ValueError('Model response exceeded max_tokens')
        if not isinstance(content, str) or not content.strip():
            raise ValueError('Empty model response')
        return json.loads(content, parse_constant=reject_constant) if schema is not None else content


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Endpoint redirects are disabled')


class MockClient:
    """Explicit fixture responses; never presented as model inference."""
    context_size = 16384

    def complete(self, messages, options, schema=None):
        if schema is None:
            return 'Hello from the mock fixture.'
        data = json.loads(messages[-1]['content'])
        if set(schema['properties']) == {'facts'}:
            facts = list(data['previous_facts'])
            if 'initial_scene' in data:
                facts.append(data['initial_scene']['beginning_narration'])
            for turn in data.get('turns', []):
                facts.append(f"Turn {turn['index']}: {turn['name']} said {turn['speech']}")
            for thought in data.get('thoughts', []):
                facts.append(f"Turn {thought['index']}: {thought['thought']}")
            return {'facts': facts}
        if 'ending_narration' in schema['properties']:
            return {'ending_narration': '窓辺の柔らかな光の中、二人を包む穏やかな時間が流れていた。'}
        if 'non_participants' in schema['properties']:
            participants = {c['name'] for c in data['characters']}
            absent = sorted(set(data.get('mentioned_non_participants', [])) - participants)
            return {'situation': data['situation'], 'goal': data.get('goal') or '', 'non_participants': absent,
                    'beginning_narration': '放課後の静かなカフェで、二人は向かい合って座っていた。'}
        if 'progress' in schema['properties']:
            number = data.get('completed_turns', len(data['history'])) + 1
            names = [c['name'] for c in data['characters']]
            progress = 'Achieved' if data['goal'] and number >= 3 else ('Approaching' if data['goal'] else 'Unchanged')
            reason = 'Mock fixture reaches its goal at turn three.' if data['goal'] else 'No goal supplied.'
            if isinstance(data['goal'], dict):
                progress = {name: 'Achieved' if number >= 3 else 'Approaching' for name in data['goal']}
                reason = {name: 'Mock fixture reaches its goal at turn three.' for name in data['goal']}
            return {
                'narration': '静かな店内で、会話が続いた。',
                'current_situation': 'The participants are talking in the cafe.',
                'progress': progress,
                'progress_reason': reason,
                'speaker_recommendation': {'name': data.get('speaker_context', {}).get('candidates', names)[0], 'confidence': 0.9 if number % 2 else 0.2},
            }
        if schema:
            return {'speech': '今日は静かですね。', 'thought': '少し緊張するな。', 'narration': '窓の外に目を向けた。', 'expression': '控えめな笑顔'}
        return 'Hello from the mock fixture.'


LocalClient = LlamaClient
