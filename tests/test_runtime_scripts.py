"""Lifecycle safety and startup failure behavior without a Docker daemon."""
import argparse
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('runtime_scripts', Path(__file__).resolve().parents[1] / 'scripts' / '_runtime.py')
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        for name in ('start_worker', 'stop_worker'):
            patcher = patch.object(runtime, name)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_foreign_container_is_never_stopped(self):
        with patch.object(runtime, 'docker', side_effect=[runtime.NAME, '[{"Config":{"Labels":{}},"State":{"Running":true}}]']) as docker:
            with self.assertRaisesRegex(RuntimeError, '管理対象'):
                runtime.stop(argparse.Namespace())
        self.assertEqual(docker.call_count, 2)
        self.assertFalse(any(call.args[0] in ('stop', 'rm') for call in docker.call_args_list))

    def test_stop_is_idempotent(self):
        with patch.object(runtime, 'inspect', return_value=None), patch.object(runtime, 'docker') as docker:
            runtime.stop(argparse.Namespace())
        docker.assert_not_called()

    def test_stop_removes_only_owned_container(self):
        with patch.object(runtime, 'inspect', return_value={'State': {'Running': True}}), patch.object(runtime, 'docker') as docker:
            runtime.stop(argparse.Namespace())
        self.assertEqual([call.args for call in docker.call_args_list], [('stop', '--time', '30', runtime.NAME), ('rm', runtime.NAME)])

    def test_busy_port_does_not_create_container(self):
        import socket
        with tempfile.NamedTemporaryFile() as model, socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            config = {'model': model.name, 'port': sock.getsockname()[1]}
            with patch.object(runtime, 'load', return_value=config), patch.object(runtime, 'inspect', return_value=None), patch.object(runtime, 'docker') as docker:
                with self.assertRaisesRegex(RuntimeError, '使用中'):
                    runtime.start(argparse.Namespace(timeout=1))
            docker.assert_not_called()

    def test_already_running_does_not_duplicate(self):
        with tempfile.NamedTemporaryFile() as model:
            with patch.object(runtime, 'load', return_value={'model': model.name}), patch.object(runtime, 'inspect', return_value={'State': {'Running': True}, 'Config': {'Labels': {'io.chatwell.settings': '{"port":8080}'}}}), patch.object(runtime, 'health', return_value=True), patch.object(runtime, 'status') as status, patch.object(runtime, 'docker') as docker:
                runtime.start(argparse.Namespace(timeout=1))
            status.assert_called_once()
            docker.assert_not_called()

    def test_failed_start_reports_cleanup_and_logs(self):
        import socket
        with tempfile.NamedTemporaryFile() as model, socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
            sock.close()
            config = {'model': model.name, 'port': port, 'context_size': 16384, 'gpu_layers': 99, 'timeout': 1, 'enable_thinking': True, 'reasoning_format': 'auto', 'reasoning_budget': 4096, 'image': runtime.IMAGE}
            with patch.object(runtime, 'load', return_value=config), patch.object(runtime, 'inspect', side_effect=[None, {'State': {'Running': False}}]), patch.object(runtime, 'docker') as docker, patch.object(runtime.subprocess, 'run') as logs:
                with self.assertRaisesRegex(RuntimeError, '終了'):
                    runtime.start(argparse.Namespace(timeout=1))
                command = docker.call_args.args
                self.assertEqual(command[command.index('--reasoning-budget') + 1], '4096')
                self.assertNotIn('--reasoning-effort', command)
                logs.assert_called_once_with(['docker', 'logs', '--tail', '30', runtime.NAME], check=False)


class EnvTests(unittest.TestCase):
    def test_precedence_and_relative_model(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '.env'
            config.write_text('CHATWELL_MODEL="models/test model.gguf"\nCHATWELL_PORT=8081\n')
            with patch.object(runtime, 'CONFIG', config), patch.dict(runtime.os.environ, {'CHATWELL_PORT': '8082'}, clear=True):
                loaded = runtime.load(argparse.Namespace(port=8083))
            self.assertEqual(loaded['port'], 8083)
            self.assertEqual(loaded['model'], str(runtime.ROOT / 'models/test model.gguf'))

    def test_missing_model_and_bad_number_are_errors(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '.env'
            with patch.object(runtime, 'CONFIG', config), patch.dict(runtime.os.environ, {}, clear=True):
                with self.assertRaisesRegex(ValueError, 'CHATWELL_MODEL'):
                    runtime.load()
                config.write_text('CHATWELL_MODEL=test.gguf\nCHATWELL_PORT=oops\n')
                with self.assertRaisesRegex(ValueError, 'CHATWELL_PORT'):
                    runtime.load()

    def test_status_uses_running_settings_even_with_invalid_env(self):
        config = {'model': '/actual/model.gguf', 'port': 8080, 'context_size': 4096}
        import json
        info = {'Config': {'Labels': {'io.chatwell.settings': json.dumps(config)}}, 'State': {'Running': True}}
        with patch.object(runtime, 'inspect', return_value=info), patch.object(runtime, 'health', return_value=True), patch.object(runtime, 'load', side_effect=AssertionError('must not read edited env')), patch('builtins.print') as output:
            runtime.status(argparse.Namespace())
        self.assertIn('/actual/model.gguf', output.call_args.args[0])

    def test_reasoning_budget_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '.env'
            with patch.object(runtime, 'CONFIG', config), patch.dict(runtime.os.environ, {}, clear=True):
                for budget in (-1, 0, 4096):
                    config.write_text(f'CHATWELL_MODEL=test.gguf\nCHATWELL_REASONING_BUDGET={budget}\n')
                    self.assertEqual(runtime.load()['reasoning_budget'], budget)
                for budget in ('-2', 'true', '1.5'):
                    config.write_text(f'CHATWELL_MODEL=test.gguf\nCHATWELL_REASONING_BUDGET={budget}\n')
                    with self.assertRaisesRegex(ValueError, 'CHATWELL_REASONING_BUDGET'):
                        runtime.load()

    def test_env_values_are_literal_and_not_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / '.env'
            config.write_text('CHATWELL_MODEL="$(touch unwanted)"\n')
            self.assertEqual(runtime.read_env(config)['CHATWELL_MODEL'], '$(touch unwanted)')


if __name__ == '__main__':
    unittest.main()
