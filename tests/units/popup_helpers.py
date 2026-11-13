from optics_framework.common.models import PopupRule


def rule_dict(name="Rate Us", **over):
    return {"name": name, "priority": 10, "detect": {"text_contains": ["rate us"]},
            "action": {"type": "tap", "target": "Maybe Later"}, **over}


def rule(name="Rate Us", **over):
    return PopupRule(**rule_dict(name, **over))
