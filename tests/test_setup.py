import unittest
from types import SimpleNamespace as Item
from unittest.mock import Mock, patch

from schedule_scanner.bootstrap import MODEL, LocalRuntime, ensure_model


class ModelSetupTests(unittest.TestCase):
    def test_installed_model_does_not_download(self):
        client = Mock()
        client.list.return_value = Item(models=[Item(model=MODEL)])
        self.assertTrue(ensure_model(client, False, Mock()))
        client.pull.assert_not_called()

    def test_missing_model_requires_download_action(self):
        client = Mock()
        client.list.return_value = Item(models=[])
        self.assertFalse(ensure_model(client, False, Mock()))
        client.pull.assert_not_called()

    def test_download_reports_progress_and_verifies_install(self):
        client = Mock()
        client.list.side_effect = [Item(models=[]), Item(models=[Item(model=MODEL)])]
        client.pull.return_value = [Item(total=100, completed=50, status="Downloading")]
        progress = Mock()
        self.assertTrue(ensure_model(client, True, progress))
        self.assertEqual(progress.call_args.args[1], 0.5)

    def test_incomplete_download_is_retryable(self):
        client = Mock()
        client.list.return_value = Item(models=[])
        client.pull.return_value = []
        with self.assertRaisesRegex(RuntimeError, "retry"):
            ensure_model(client, True, Mock())
        client.list.side_effect = [Item(models=[]), Item(models=[Item(model=MODEL)])]
        self.assertTrue(ensure_model(client, True, Mock()))

    def test_network_failure_is_not_reported_as_success(self):
        client = Mock()
        client.list.return_value = Item(models=[])
        client.pull.side_effect = ConnectionError("Offline")
        with self.assertRaises(ConnectionError):
            ensure_model(client, True, Mock())

    @patch("schedule_scanner.bootstrap.subprocess.run")
    def test_cleanup_targets_only_owned_process_tree(self, run):
        runtime = LocalRuntime()
        process = Mock(pid=98765)
        process.poll.return_value = None
        runtime.process = process
        runtime.stop()
        self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "98765", "/T", "/F"])
        self.assertTrue(runtime.cancelled.is_set())
        self.assertIsNone(runtime.process)

    @patch("schedule_scanner.bootstrap.subprocess.run")
    def test_cleanup_without_owned_process_leaves_ollama_alone(self, run):
        LocalRuntime().stop()
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
