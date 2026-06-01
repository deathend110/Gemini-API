from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class TestGatewayUvStartupDocs(unittest.TestCase):
    def test_gateway_env_script_points_to_uv_run_startup(self) -> None:
        script = (ROOT / "gateway" / "set_gateway_env.ps1").read_text(
            encoding="utf-8"
        )

        self.assertIn("uv run python -m gateway.main", script)

    def test_gateway_readme_uses_uv_startup_commands(self) -> None:
        readme = (ROOT / "gateway" / "README.md").read_text(encoding="utf-8")

        self.assertIn("uv sync", readme)
        self.assertIn("uv run python -m gateway.main", readme)
        self.assertNotIn("pip install -e .", readme)


if __name__ == "__main__":
    unittest.main()
