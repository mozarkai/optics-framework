from types import SimpleNamespace

import pytest

from optics_framework.common.base_factory import InstanceFallback


def _instance(name, fail=False):
    def call():
        if fail:
            raise RuntimeError(name)
        return name
    return SimpleNamespace(call=call)


def test_calls_fall_back_in_order():
    fallback = InstanceFallback([_instance("first", fail=True), _instance("second")])
    assert fallback.call() == "second"


def test_routed_to_sends_calls_only_to_that_instance():
    first, second = _instance("first"), _instance("second")
    fallback = InstanceFallback([first, second])
    with fallback.routed_to(second):
        assert fallback.call() == "second"
    assert fallback.call() == "first"


def test_routed_to_does_not_fall_back_to_other_instances():
    first, second = _instance("first"), _instance("second", fail=True)
    fallback = InstanceFallback([first, second])
    with fallback.routed_to(second), pytest.raises(RuntimeError, match="second"):
        fallback.call()


def test_routed_to_an_unknown_instance_keeps_the_fallback_order():
    first, second = _instance("first", fail=True), _instance("second")
    fallback = InstanceFallback([first, second])
    with fallback.routed_to(_instance("elsewhere")):
        assert fallback.call() == "second"


def test_routed_to_restores_the_instances_when_the_block_raises():
    first, second = _instance("first"), _instance("second")
    fallback = InstanceFallback([first, second])
    with pytest.raises(ValueError), fallback.routed_to(second):
        raise ValueError
    assert fallback.instances == [first, second]
