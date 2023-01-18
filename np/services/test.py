from __future__ import annotations

import sys
import typing
from typing import Any, Protocol, Sequence


class TestFailure(AssertionError): ...
    
# -------------------------------------------------------------------------------------- #
# verb-based protocols define functions called at specific times relative to each other,
# so that the order they're called in is fixed, 
#   e.g. initialize() | test() | start() | verify() | stop() | finalize() | validate()

@typing.runtime_checkable
class Testable(Protocol):
    "Supports `test()`: without creating new data, quickly confirms readiness for use, or raises `TestFailure`. Always called before first use. See `PreTestable` for comprehensive test."
    def test() -> None: ... # args aren't checked, only the method name
    
# -------------------------------------------------------------------------------------- #
    
class ClassWithTestMethod:
    def test(): ...

assert isinstance(ClassWithTestMethod, Testable)

class ClassWithTestProperty:
    test = '?'
assert isinstance(ClassWithTestProperty, Testable)
    
class ClassWithoutTest: ...
assert not isinstance(ClassWithoutTest, Testable)

def test(): ...
assert isinstance(sys.modules[__name__], Testable)
test = 3
assert isinstance(sys.modules[__name__], Testable)

@typing.runtime_checkable
class Testable(Protocol):
    test: Any # in fact, any attribute will suffice!

assert isinstance(ClassWithTestMethod, Testable)
assert isinstance(ClassWithTestProperty, Testable)
assert isinstance(sys.modules[__name__], Testable)

def test_services(*services: Testable) -> AttributeError | None:
    for service in services:
        try:
            service.test()
        except AttributeError as exc:
            return exc.__class__
        else:
            return None
    
assert test_services(ClassWithTestMethod) == None
assert test_services(ClassWithoutTest) == AttributeError