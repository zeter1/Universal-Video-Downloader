"""Small thread-safe containers shared across features."""

from threading import Lock


class ThreadSafeList:
    def __init__(self):
        self._list = []
        self._lock = Lock()

    def append(self, item):
        with self._lock:
            self._list.append(item)

    def remove(self, item):
        with self._lock:
            if item in self._list:
                self._list.remove(item)

    def __len__(self):
        with self._lock:
            return len(self._list)

    def __iter__(self):
        with self._lock:
            return iter(self._list.copy())

    def clear(self):
        with self._lock:
            self._list.clear()

    def __getitem__(self, index):
        with self._lock:
            return self._list[index]

    def copy(self):
        with self._lock:
            return self._list.copy()


class SafeCounter:
    def __init__(self, initial=0):
        self._value = initial
        self._lock = Lock()

    def increment(self, amount=1):
        with self._lock:
            self._value += amount
            return self._value

    def value(self):
        with self._lock:
            return self._value

    def reset(self, val=0):
        with self._lock:
            self._value = val
