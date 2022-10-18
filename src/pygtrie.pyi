"""Implements stubs for pygtrie."""
import collections as _abc
from typing import (
    Any, Set, TypeVar, Iterator, Literal, NoReturn, Union, Type,
    Generic, MutableMapping, overload, Mapping, Iterable, Callable,
)

__version__: str
KeyT = TypeVar('KeyT')
ValueT = TypeVar('ValueT')
Key2T = TypeVar('Key2T')
Value2T = TypeVar('Value2T')
T = TypeVar('T')
TrieT = TypeVar('TrieT', bound=Trie)
_EMPTY: _NoChildren

class ShortKeyError(KeyError): ...

class _NoChildren(Iterator[Any]):
    def __bool__(self) -> Literal[False]: ...
    def __nonzero__(self) -> Literal[False]: ...
    def __len__(self) -> Literal[0]: ...
    def __iter__(self) -> _NoChildren: ...
    def iteritems(self) -> _NoChildren: ...
    def sorted_items(self) -> _NoChildren: ...
    def __next__(self) -> NoReturn: ...
    def next(self) -> NoReturn: ...
    def get(self, _step: Any) -> None: ...
    def add(self, parent: Any, step: Any) -> _Node: ...
    def require(self, parent: Any, step: Any) -> _Node: ...
    def copy(self, _make_copy: Any, _queue: Any) -> _NoChildren: ...
    def __deepcopy__(self, memo: dict) -> _NoChildren: ...

class _OneChild(Generic[KeyT, ValueT]):
    step: Any = ...
    node: Any = ...
    def __init__(self, step: Any, node: Any) -> None: ...
    def __bool__(self) -> bool: ...
    def __nonzero__(self) -> bool: ...
    def __len__(self) -> int: ...
    def sorted_items(self) -> list[tuple[KeyT, ValueT]]: ...
    def iteritems(self) -> Iterator[tuple[KeyT, ValueT]]: ...
    def get(self, step: Any): ...
    def add(self, parent: Any, step: Any): ...
    def require(self, parent: Any, step: Any): ...
    def delete(self, parent: Any, _step: Any) -> None: ...
    def copy(self, make_copy: Any, queue: Any): ...

class _Children(dict):
    def __init__(self, *items: Any) -> None: ...
    def sorted_items(self) -> list[Any]: ...
    def iteritems(self) -> Iterator[Any]: ...
    def add(self, _parent: Any, step: Any): ...
    def require(self, _parent: Any, step: Any): ...
    def delete(self, parent: Any, step: Any) -> None: ...
    def copy(self, make_copy: Any, queue: Any): ...  # type: ignore

class _Node:
    children: Any = ...
    value: Any = ...
    def __init__(self) -> None: ...
    def iterate(self, path: Any, shallow: Any, iteritems: Any) -> None: ...
    def traverse(self, node_factory: Any, path_conv: Any, path: Any, iteritems: Any): ...
    def equals(self, other: Any): ...
    __bool__: Any = ...
    __nonzero__: Any = ...
    __hash__: Any = ...
    def shallow_copy(self, make_copy: Any): ...
    def copy(self, make_copy: Any): ...

AnyNode = Union[_Node, _NoChildren, _OneChild]

class Trie(MutableMapping[KeyT, ValueT], Generic[KeyT, ValueT]):
    def __init__(self, *args: Any, **kwargs: Any) -> None: ...
    def enable_sorting(self, enable: bool = ...) -> None: ...
    def clear(self) -> None: ...

    # implements update(), same as superclass.

    def copy(self: TrieT, __make_copy: Callable[[T], T] = ...) -> TrieT: ...
    def __copy__(self: TrieT) -> TrieT: ...
    def __deepcopy__(self: TrieT, memo: dict) -> TrieT: ...

    @overload
    @classmethod
    def fromkeys(cls: Type[TrieT], keys: Iterable[KeyT]) -> TrieT: ...
    @overload
    @classmethod
    def fromkeys(cls: Type[TrieT], keys: Iterable[KeyT], value: ValueT) -> TrieT: ...

    def __iter__(self) -> Iterator[KeyT]: ...
    def iteritems(self, prefix: KeyT = ..., shallow: bool = ...) -> Iterator[tuple[KeyT, ValueT]]: ...
    def iterkeys(self, prefix: KeyT = ..., shallow: bool = ...) -> Iterator[KeyT]: ...
    def itervalues(self, prefix: KeyT = ..., shallow: bool = ...) -> Iterator[ValueT]: ...
    def items(self, prefix: KeyT = ..., shallow: bool = ...) -> list[tuple[KeyT, ValueT]]: ...  # type: ignore  # Py2
    def keys(self, prefix: KeyT = ..., shallow: bool = ...) -> list[KeyT]: ...  # type: ignore  # Py2
    def values(self, prefix: KeyT = ..., shallow: bool = ...) -> list[ValueT]: ...  # type: ignore  # Py2
    def __len__(self) -> int: ...
    def __bool__(self) -> bool: ...
    def __nonzero__(self) -> bool: ...
    __hash__ = None
    HAS_VALUE: int
    HAS_SUBTRIE: int
    def has_node(self, key: KeyT): ...
    def has_key(self, key: KeyT): ...
    def has_subtrie(self, key: KeyT): ...
    # TODO: slice can't specify it must always be slice(KeyT, None, None)
    def __getitem__(self, key_or_slice: KeyT | slice): ...
    def __setitem__(self, key_or_slice: KeyT | slice, value: ValueT) -> None: ...
    def setdefault(self, key: KeyT, default: ValueT = None) -> ValueT: ...
    @overload
    def pop(self, key: KeyT) -> ValueT: ...
    @overload
    def pop(self, key: KeyT, default: ValueT | T = ...) -> ValueT | T: ...
    def popitem(self) -> tuple[KeyT, ValueT]: ...
    def __delitem__(self, key_or_slice: KeyT | slice) -> None: ...

    class _Step(Generic[Key2T, Value2T]):
        def __init__(self, trie: Trie, path: Key2T, pos: int, node: AnyNode) -> None: ...
        def __bool__(self) -> bool: ...
        def __nonzero__(self) -> bool: ...
        def __getitem__(self, index: int) -> Value2T: ...

        @property
        def is_set(self) -> bool: ...
        @property
        def has_subtrie(self) -> bool: ...

        def get(self, default: T = None) -> Union[Value2T, T]: ...
        def set(self, value: Value2T) -> None: ...
        def setdefault(self, value: Value2T) -> Value2T: ...
        @property
        def key(self) -> Key2T: ...
        @property
        def value(self) -> Value2T: ...
        @value.setter
        def value(self, value: Value2T) -> None: ...
    class _NoneStep(_Step[None, None]): ...

    def walk_towards(self, key: KeyT) -> Iterator[_Step[KeyT, ValueT]]: ...
    def prefixes(self, key: KeyT) -> Iterator[_Step[KeyT, ValueT]]: ...
    def shortest_prefix(self, key: KeyT): ...
    def longest_prefix(self, key: KeyT): ...
    def __eq__(self, other: object) -> bool: ...
    def __ne__(self, other: object) -> bool: ...
    def traverse(self, node_factory: Callable[..., T], prefix: KeyT = ...) -> T: ...

class CharTrie(Trie[str, ValueT], Generic[ValueT]): ...

class StringTrie(Trie[str, ValueT], Generic[ValueT]):
    def __init__(self, *args: Any, separator: str='/', **kwargs: Any) -> None: ...

    @overload  # type: ignore  # Incompatible override
    @classmethod
    def fromkeys(cls, keys: Iterable[str], *, separator: str = ...) -> StringTrie[None]: ...
    @overload
    @classmethod
    def fromkeys(cls, keys: Iterable[str], value: ValueT, separator: str = ...) -> StringTrie[ValueT]: ...

class PrefixSet(Set[KeyT], Generic[KeyT]):
    # TODO: Used as factory(**kwargs), but can't express that.
    def __init__(self, iterable: Iterable[KeyT] = ..., factory: Callable[..., Trie] = ..., **kwargs: Any) -> None: ...
    def copy(self) -> PrefixSet[KeyT]: ...
    def __copy__(self) -> PrefixSet[KeyT]: ...
    def __deepcopy__(self, memo: dict) -> PrefixSet[KeyT]: ...
    def clear(self) -> None: ...
    def __contains__(self, key: object) -> bool: ...
    def __iter__(self) -> Iterator[KeyT]: ...
    def iter(self, prefix: KeyT = ...) -> Iterator[KeyT]: ...
    def __len__(self) -> int: ...
    def add(self, value: KeyT) -> None: ...
    # Not implemented.
    def discard(self, value: KeyT) -> NoReturn: ...
    def remove(self, value: KeyT) -> NoReturn: ...
    def pop(self) -> NoReturn: ...
