from __future__ import annotations

import importlib
from pathlib import Path


def test_ai_promotion_gate_invoked_in_ansible_promotion_path() -> None:
    yaml = importlib.import_module("yaml")
    repo_root = Path(__file__).resolve().parents[1]
    playbook = repo_root / "deploy/ansible/pioneer_security/10-ai-training.yml"
    parsed = yaml.safe_load(playbook.read_text(encoding="utf-8"))
    assert isinstance(parsed, list)
    assert parsed, "Ansible playbook must contain at least one play"
    play = parsed[0]
    assert isinstance(play, dict)
    tasks = play.get("tasks")
    assert isinstance(tasks, list)

    promote_task = next(
        (
            task
            for task in tasks
            if isinstance(task, dict)
            and task.get("name") == "Deploy promotion runner"
        ),
        None,
    )
    assert isinstance(promote_task, dict), "Deploy promotion runner task missing"
    copy_block = promote_task.get("ansible.builtin.copy")
    assert isinstance(copy_block, dict)
    script = copy_block.get("content")
    assert isinstance(script, str)

    gate_idx = script.find("tools/ai_promotion_gate.py")
    promote_idx = script.find("tools/ai_promote_model.py")
    assert gate_idx != -1, "Promotion gate invocation missing from promotion runner"
    assert promote_idx != -1, "Model promotion command missing from promotion runner"
    assert gate_idx < promote_idx, "Promotion gate must execute before model promotion"
    for required_arg in (
        '--model-id "$EGREGORE_AI_PROMOTION_MODEL_ID"',
        '--artifact "$EGREGORE_AI_APPROVAL_ARTIFACT"',
        '--signature "$EGREGORE_AI_APPROVAL_SIGNATURE"',
        '--public-key "$EGREGORE_AI_APPROVAL_PUBKEY"',
        '--checkpoints-dir "$EGREGORE_AI_CHECKPOINTS_DIR"',
    ):
        assert required_arg in script
