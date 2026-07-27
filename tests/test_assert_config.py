from importlib import import_module


def test_assert_callable_resolves():
    target = import_module("evaluation.assert_suite.target")

    assert callable(target.chat)

