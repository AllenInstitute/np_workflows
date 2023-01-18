from __future__ import annotations

import asyncio
import inspect
import time
import typing
import weakref
from abc import ABC, ABCMeta, abstractmethod
from collections import defaultdict
from typing import Any, Generic, Optional, Protocol, Sequence, Type, TypeVar, Union


class MetaInstanceRegistry(ABCMeta):
    """Metaclass providing an instance registry"""

    def __init__(cls, *args, **kwargs):
        # Create classh
        super().__init__(*args, **kwargs)

        # Initialize fresh instance storage
        cls._instances = weakref.WeakSet()

    def __call__(cls, *args, **kwargs):
        # Create instance (calls __init__ and __new__ methods)
        inst = super().__call__(*args, **kwargs)

        # Store weak reference to instance. WeakSet will automatically remove
        # references to objects that have been garbage collected
        cls._instances.add(inst)

        return inst
    
    def all_instances(cls, subclasses=True, supporting:Optional[type]=None) -> tuple[object,...]:
        """Get all instances of this class in the registry. 
        - `subclasses=True` returns instacnes of subclasses recursively.
        - `supporting=Protocol`, only return instances supporting the given protocol(s).
        """
        all_instances = list(cls._instances)
        if subclasses:
            for child in cls.__subclasses__():
                all_instances += child.all_instances(subclasses=subclasses)
                
        if supporting:
            all_instances = [inst for inst in all_instances if isinstance(inst, typing.get_args(supporting) or supporting)]

        # Remove duplicates from multiple inheritance.
        return tuple(set(all_instances))

def test_instance_registry() -> None:
    class Foo(metaclass=MetaInstanceRegistry): ...
    class Bar(Foo): ...
    z = Bar()
    assert len(all_bar := Bar.all_instances(subclasses=False)) == 1, 'Expected instance not found in instance-registry'
    assert z in all_bar, 'Expected instance not found in instance-registry: object identity might not be preserved'
    x = Foo()
    y = Foo()
    assert len(Foo.all_instances(subclasses=False)) <= 2, 'Unexpected instance found in instance-registry: excluding subclasses not functioning correctly'
    assert len(all_foo := Foo.all_instances(subclasses=True)) >= 2, 'Expected instances not found in instance-registry'
    assert len(all_foo) == 3, 'Expected instances not found in instance-registry: recursive search through subclasses not functioning correctly'
    assert x in all_foo and y in all_foo and z in all_foo, 'Expected instances not found in instance-registry: object identity might not be preserved'
    
class Service(ABC, metaclass=MetaInstanceRegistry):
    
    def __new__(cls, *arg, **kwargs):
        # get coroutines
        parent_coros = inspect.getmembers(Service, predicate=inspect.iscoroutinefunction)

        # check if parent's coros are still coros in a child
        for coro in parent_coros:
            child_method = getattr(cls, coro[0])
            if not inspect.iscoroutinefunction(child_method):
                raise RuntimeError(f'The overloaded method {child_method} must be a coroutine as per its baseclass')
        return super().__new__(cls, *arg, **kwargs)

    async def initialize(self) -> None:
        "On startup: all config, connections, etc. Always called before first use."
        print(f'initialize: {self.__class__.__name__}')
        
    @abstractmethod
    async def test(self) -> None:
        "Tests the service is functional. Always called before first use."
        
    async def shutdown(self) -> None:
        "All cleanup, file management, etc. Always called after final use."
        print(f'shutdown: {self.__class__.__name__}')
        
@typing.runtime_checkable
class Testable(Protocol):
    async def test(self) -> None:
        "Basic test of functionality. Always called before first use. See `PreTestable` for thorough test."
@typing.runtime_checkable
class PreTestable(Protocol):
    async def pretest(self) -> None:
        "Optional comprehensive test of functionality."
@typing.runtime_checkable
class Prepareable(Protocol):
    async def prepare(self) -> None:
        "Make ready for imminent use. Re-arm, run checks, etc. Always called before use."
@typing.runtime_checkable
class Startable(Protocol):
    def start(self) -> None:
        "Start or re-start. Always called at point of use and before `stop`."
@typing.runtime_checkable
class Stoppable(Protocol):
    def stop(self) -> None:
        "Stop or pause. Always called when stopping use and after `start`."
@typing.runtime_checkable
class Finalizable(Protocol):
    async def finalize(self) -> None:
        "Handle previous use. Cleanup, file management, etc. Always called after use."

PreExperimentProtocols = Union[Testable, PreTestable, Prepareable, Startable]
PostExperimentProtocols = Union[Stoppable, Finalizable]

class PT(Protocol):
    async def pretest(self) -> None: ...
    async def test(self) -> None: ...
    
class TestableStartable(Testable, Startable): ...

async def test_services(services: Sequence[Service]):
    test = pretest = prepare = finalize = []
    for service in services:
        if isinstance(service, Testable):
            test += asyncio.create_task(service.test())
        if isinstance(service, PreTestable):
            pretest += asyncio.create_task(service.pretest())
        if isinstance(service, Prepareable):
            prepare += asyncio.create_task(service.prepare())
        if isinstance(service, Startable):
            start = service.start
        if isinstance(service, Stoppable):
            stop = service.stop
        if isinstance(service, Finalizable):
            finalize += asyncio.create_task(service.finalize())
        # await test, pretest, prepare
        for pre_exp_coros in [pretest, prepare]:
            await pre_exp_coros
        start()
        stop()
        for post_exp_coros in [finalize]:
            await post_exp_coros
    
class ServiceWithProtocols(Service, ABC):
    async def initialize(self) -> None: await asyncio.sleep(5); print('Initialized')
    async def test(self) -> None: await asyncio.sleep(5); print('Tested')
    async def pretest(self: PreTestable) -> None: await asyncio.sleep(5); print('PreTested')
    async def prepare(self) -> None: await asyncio.sleep(5); print('Prepared')
    def start(self) -> None: pass; print('Started')
    def stop(self) -> None: pass; print('Stopped')
    async def finalize(self) -> None: await asyncio.sleep(5); print('Finalized')
    # async def shutdown(self) -> None: ...
    
class Ephys(Service, ABC):
    @abstractmethod
    async def test(self) -> None: ...
    @abstractmethod
    async def initialize(self) -> None: ...
    @abstractmethod
    def pretest(self) -> None: ...
    
class EphysTest(Ephys):
    async def test(self) -> None: print('ephys test')
    async def initialize(self) -> None: print('ephys initialize')
    def pretest(self) -> None: print('ephys pretest')
    
class Database(Service, ABC):
    @abstractmethod
    async def test(self) -> None: ...
    
class Stim(Service, ABC):
    @abstractmethod
    async def test(self) -> None: ...
    @abstractmethod
    def pretest(self) -> None: ...
    @abstractmethod
    def start(self) -> None: ...
    
class Video(Service, ABC):
    @abstractmethod
    async def test(self) -> None: ...
    @abstractmethod
    def pretest(self) -> None: ...
    @abstractmethod
    def start(self) -> None: ...
    
class Photo(Service, ABC):
    @abstractmethod
    async def test(self) -> None: ...
    @abstractmethod
    def pretest(self) -> None: ...
    @abstractmethod
    def take_snapshot(self) -> None: ...
    
class DAQ(Service, ABC):
    @abstractmethod
    async def test(self) -> None: ...
    @abstractmethod
    def pretest(self) -> None: ...
    @abstractmethod
    def start(self) -> None: ...

def test_service_protocols():

    # all Service classes must implement Testable protocol
    instances: set[Service] = set()
    for cls in Service.__subclasses__():
        assert issubclass(cls, Testable), f'cls {cls.__name__} is not Testable'
        try:
            instances.add(cls())
        except TypeError:
            pass # some classes are abstract and cannot be instantiate

    # all Service instances must be Testable
    assert all(inst in Service.all_instances() for inst in instances), 'Expected instances not found in instance-registry'
    for inst in instances:
        assert isinstance(inst, Testable), f'{inst.__class__.__name__} instance is not Testable'

    # some Services support addtl protocols to be run pre- or post-experiment
    protocols = (*typing.get_args(Union[PreExperimentProtocols, PostExperimentProtocols]),)
    
    # the following demonstrate different ways of checking if a class supports a protocol
    from_issubclass = from_isinstance = from_all_instances = defaultdict(set)
    for cls in (inst.__class__ for inst in Service.all_instances(subclasses=True)):
        if any(protocols_supported := tuple(protocol for protocol in protocols if issubclass(cls, protocol))):
            protocol_names = tuple(protocol.__name__ for protocol in protocols_supported)
            from_issubclass[cls.__name__].update(protocol_names)
            
        if any(protocols_supported := tuple(protocol for protocol in protocols if isinstance(cls, protocol))):
            protocol_names = tuple(protocol.__name__ for protocol in protocols_supported)
            from_isinstance[cls.__name__].update(protocol_names)
            
    for protocol in protocols:
        if supports_protocol := Service.all_instances(subclasses=True, supporting=protocol):
            for inst in supports_protocol:
                from_all_instances[inst.__class__.__name__].add(protocol.__name__)
                
    assert from_issubclass == from_isinstance == from_all_instances, f'Different methods of checking for supported protocols yield different results {from_issubclass} != {from_all_instances} != {from_all_instances}'

    reverse_map = defaultdict(set)
    for k,v in from_all_instances.items():
        for vv in v:
            reverse_map[vv].add(k)
    for k,v in reverse_map.items():
        print(f'{k} protocol supported by: {", ".join(v)}', flush=True)
        

test_instance_registry()
test_service_protocols()

asyncio.run(
    test_services(
        (
            ServiceWithProtocols(),
            ServiceWithProtocols(),
            )
        )
    )
# def run_exp():
#     for inst in Service.all_instances(subclasses=True, supporting=PRE_EXPERIMENT_PROTOCOLS):
#         inst.initialize()
        
# if __name__ == '__main__':
    
    # run_exp()