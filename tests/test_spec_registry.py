import json
import unittest
from pathlib import Path

VALID_STATUSES = {"draft", "approved", "building", "blocked", "done"}
STALE_SPECS = {"2026-07-26-evidence-to-intelligence-mvp", "2026-07-25-simplify-radar", "2026-07-23-multi-source-intelligence-radar"}

def validate_registry(data: dict, repo_root: Path) -> None:
    # Unknown spec path
    for spec in data.get("specs", []):
        spec_path = repo_root / spec["path"]
        if not spec_path.is_file():
            raise ValueError(f"Unknown spec path: {spec['path']}")
        
        # Invalid status
        if spec.get("status") not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {spec.get('status')}")
        
        # Stale conflicting specs having status approved or building
        if spec.get("id") in STALE_SPECS and spec.get("status") in {"approved", "building"}:
            raise ValueError(f"Stale spec {spec.get('id')} cannot be approved or building")

    # More than one spec with status building
    building_count = sum(1 for spec in data.get("specs", []) if spec.get("status") == "building")
    if building_count > 1:
        raise ValueError("More than one spec with status building")

    # Build dependency graph
    deps = data.get("dependencies", [])
    graph = {}
    reverse_graph = {}
    for spec in data.get("specs", []):
        graph[spec["id"]] = []
        reverse_graph[spec["id"]] = spec
        
    for d in deps:
        if d["from"] in graph and d["to"] in graph:
            graph[d["from"]].append(d["to"])
            
            # Dependency on a later wave
            from_spec = reverse_graph[d["from"]]
            to_spec = reverse_graph[d["to"]]
            from_wave = from_spec.get("release_wave")
            to_wave = to_spec.get("release_wave")
            if from_wave is not None and to_wave is not None:
                if from_wave < to_wave:
                    raise ValueError(f"Dependency on a later wave: {from_spec['id']} (wave {from_wave}) depends on {to_spec['id']} (wave {to_wave})")

    # Check for cycles using DFS
    visited = set()
    rec_stack = set()
    def is_cyclic(node):
        visited.add(node)
        rec_stack.add(node)
        for neighbor in graph.get(node, []):
            if neighbor not in visited:
                if is_cyclic(neighbor):
                    return True
            elif neighbor in rec_stack:
                return True
        rec_stack.remove(node)
        return False

    for node in graph:
        if node not in visited:
            if is_cyclic(node):
                raise ValueError("Dependency cycle detected")


class SpecRegistryTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(__file__).parent.parent
        self.registry_path = self.repo_root / "specs" / "production-program.json"

    def test_registry_contract(self):
        with open(self.registry_path) as f:
            data = json.load(f)
            
        self.assertIn("version", data)
        self.assertIn("schema_version", data)
        self.assertIn("specs", data)
        self.assertIn("dependencies", data)
        self.assertIn("current_wave", data)
        self.assertIn("external_blockers", data)
        
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(len(data["specs"]), 12)
        
        for spec in data["specs"]:
            self.assertIn("id", spec)
            self.assertIn("path", spec)
            self.assertIn("status", spec)
            self.assertIn("risk_tier", spec)
            self.assertIn("release_wave", spec)
            self.assertIn("date", spec)
            self.assertTrue((self.repo_root / spec["path"]).is_file())

    def test_invalid_registries(self):
        with open(self.registry_path) as f:
            base_data = json.load(f)

        # Unknown spec path
        data = json.loads(json.dumps(base_data))
        data["specs"][0]["path"] = "specs/nonexistent-file.md"
        with self.assertRaisesRegex(ValueError, "Unknown spec path"):
            validate_registry(data, self.repo_root)

        # Invalid status
        data = json.loads(json.dumps(base_data))
        data["specs"][0]["status"] = "invalid_status"
        with self.assertRaisesRegex(ValueError, "Invalid status"):
            validate_registry(data, self.repo_root)

        # More than one spec with status building
        data = json.loads(json.dumps(base_data))
        data["specs"][0]["status"] = "building"
        data["specs"][1]["status"] = "building"
        with self.assertRaisesRegex(ValueError, "More than one spec with status building"):
            validate_registry(data, self.repo_root)

        # Dependency cycle
        data = json.loads(json.dumps(base_data))
        data["specs"].append({"id": "cycle1", "path": "specs/2026-07-28-internal-production-program.md", "status": "approved", "release_wave": 0})
        data["specs"].append({"id": "cycle2", "path": "specs/2026-07-28-internal-production-program.md", "status": "approved", "release_wave": 0})
        data["dependencies"].append({"from": "cycle1", "to": "cycle2", "type": "requires"})
        data["dependencies"].append({"from": "cycle2", "to": "cycle1", "type": "requires"})
        with self.assertRaisesRegex(ValueError, "Dependency cycle detected"):
            validate_registry(data, self.repo_root)

        # Dependency on a later wave
        data = json.loads(json.dumps(base_data))
        data["specs"][0]["release_wave"] = 1
        data["specs"][1]["release_wave"] = 3
        data["specs"][0]["id"] = "spec1"
        data["specs"][1]["id"] = "spec3"
        data["dependencies"].append({"from": "spec1", "to": "spec3", "type": "requires"})
        with self.assertRaisesRegex(ValueError, "Dependency on a later wave"):
            validate_registry(data, self.repo_root)

        # Stale specs approved or building
        for spec_id in STALE_SPECS:
            for status in ["approved", "building"]:
                data = json.loads(json.dumps(base_data))
                for s in data["specs"]:
                    if s["id"] == spec_id:
                        s["status"] = status
                with self.assertRaisesRegex(ValueError, "Stale spec"):
                    validate_registry(data, self.repo_root)
        
    def test_program_document_matches_registry(self):
        with open(self.registry_path) as f:
            data = json.load(f)
        
        all_ids = {s["id"] for s in data["specs"]}
        for d in data["dependencies"]:
            self.assertIn(d["from"], all_ids)
            self.assertIn(d["to"], all_ids)
            
        expected_deps = [
            ("2026-07-28-medallion-lite-internal-production", "2026-07-28-internal-production-program"),
            ("2026-07-28-release-engineering-runtime-operations", "2026-07-28-internal-production-program"),
            ("2026-07-28-safe-source-content-ingestion", "2026-07-28-medallion-lite-internal-production"),
            ("2026-07-28-llm-workload-control-quality", "2026-07-28-medallion-lite-internal-production"),
            ("2026-07-28-grounded-social-briefs", "2026-07-28-medallion-lite-internal-production"),
            ("2026-07-28-grounded-social-briefs", "2026-07-28-safe-source-content-ingestion"),
            ("2026-07-28-grounded-social-briefs", "2026-07-28-llm-workload-control-quality"),
            ("2026-07-28-single-intelligence-feed-ui", "2026-07-28-grounded-social-briefs"),
        ]
        
        actual_deps = [(d["from"], d["to"]) for d in data["dependencies"]]
        for e in expected_deps:
            self.assertIn(e, actual_deps)
