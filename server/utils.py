import asyncio
import functools
import itertools


def pairwise(iterable):
    a, b = itertools.tee(iterable)
    next(b, None)
    return zip(a, b)


def clamp(x, low, high):
    if x < low:
        return low
    if x > high:
        return high
    return x


def map_range(x, old_low, old_high, new_low, new_high):
    return new_low + ((x - old_low) * (new_high - new_low) / (old_high - old_low))


def locked(func):
    lock = asyncio.Lock()

    @functools.wraps(func)
    async def wrapped(*args, **kwargs):
        async with lock:
            return await func(*args, **kwargs)

    return wrapped
