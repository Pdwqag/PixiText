import importlib
import unittest


class ManifestRemovalTest(unittest.TestCase):
    def test_get_sync_manifest_not_present(self) -> None:
        app_module = importlib.import_module("app")
        self.assertFalse(
            hasattr(app_module, "get_sync_manifest"),
            "get_sync_manifest should have been removed from app.py",
        )


if __name__ == "__main__":
    unittest.main()
