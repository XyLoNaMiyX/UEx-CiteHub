import abc
import asyncio
import json
import random
import time
import logging
import datetime
from typing import Mapping
from dataclasses import asdict, is_dataclass
from .step import Step
from ..storage import Storage


DELAY_JITTER_PERCENT = 0.05
_log = logging.getLogger(__name__)

ERROR_DELAYS = [1, 10, 60, 10 * 60, 60 * 60, 24 * 60 * 60]

# TODO tasks are a bit messy because each stores its resume state in its own way
# ideally we'd have a rust-like enum to ensure that only the data we need is saved
# (and we don't have garbage) which maybe we could return to automate the process
#
# similar for set_url which resets due and stage, maybe the tasks should define what
# they require so it can be better generalized and also have a "start state" which
# could be defined through decorators or something
class Task(abc.ABC):
    Stage = None

    # Every different external source uses its own `Task` for crawling profiles, and the
    # subclasses know how to update the profile data. Every task also has its own profile.
    def __init__(self, root):
        if not isinstance(self.Stage, type):
            raise RuntimeError("task subclass should define a nested Stage class")

        self._root = root / self.namespace()
        self._task_file = self._root / "task.json"
        self._storage = Storage(
            self._root
        )  # TODO maybe storage should have the task too?
        self._due = 0
        self._error = 0
        self._stage = self.initial_stage()

    @classmethod
    @abc.abstractmethod
    def namespace(cls) -> str:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def initial_stage(cls):
        # TODO this can probably be stage with index 0 that should have default values set
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def fields(cls) -> Mapping[str, str]:
        # Should return `{field key: field description}`` on required user-provided fields.
        # The description may contain HTML tags.
        raise NotImplementedError

    @abc.abstractmethod
    def set_field(self, key, value):
        raise NotImplementedError

    @abc.abstractmethod
    async def _step(self, stage, session) -> Step:
        # Should be stateless (no internal mutation or mutation of the input stage).
        # This way do our best to achieve atomicy and only if things go well.
        # It can rely on the storage to contain the data from previous successful steps.
        # TODO perhaps step and the data it produces should be separate?
        raise NotImplementedError

    def load(self):
        self._storage.load()

        try:
            with self._task_file.open(encoding="utf-8") as fd:
                data = json.load(fd)
        except FileNotFoundError:
            return

        delta = data.pop("due") - time.time()
        stage_index = data.pop("_index")
        self._due = asyncio.get_event_loop().time() + delta
        for field in dir(self.Stage):
            Field = getattr(self.Stage, field)
            if is_dataclass(Field) and Field.INDEX == stage_index:
                self._stage = Field(**data)

    def save(self):
        self._storage.save()

        data = asdict(self._stage)
        data["_index"] = self._stage.INDEX
        delta = self._due - asyncio.get_event_loop().time()
        data["due"] = time.time() + delta

        if not self._task_file.parent.is_dir():
            self._task_file.parent.mkdir()

        with self._task_file.open("w", encoding="utf-8") as fd:
            json.dump(data, fd)

    async def step(self, session):
        try:
            step = await self._step(self._stage, session)
        except Exception:
            delay = ERROR_DELAYS[min(self._error, len(ERROR_DELAYS) - 1)]
            self._error += 1
            _log.exception(
                "%d consecutive unhandled exception(s) stepping %s, delay for %ds",
                self._error,
                self.namespace(),
                delay,
            )

            self._due = asyncio.get_event_loop().time() + delay
            return
        else:
            self._error = 0

        if not isinstance(step, Step):
            raise TypeError(f"step returned invalid data: {step}")

        # Tasks can embed the authors where author paths should belong for convenience.
        # Address that here before saving anything so everything has the right types.
        step.fix_authors()

        # TODO we can probably have all data in memory and save it into a single-file
        #      which should make it easier to avoid partial files. it's not a lot and
        #      would save hundreds of reads when merging.
        for author in step.authors:
            self._storage.save_author(author)

        user_pub_ids = set(self._storage.user_pub_ids)

        for pub in step.self_publications:
            user_pub_ids.add(pub.id)
            self._storage.save_pub(pub)

        self._storage.user_pub_ids = list(user_pub_ids)

        for cites_pub_id, citations in step.citations.items():
            pub = self._storage.load_pub(cites_pub_id)
            if pub.cit_paths is None:
                pub.cit_paths = []

            cit_paths = set(pub.cit_paths)

            for cit in citations:
                cit_paths.add(cit.unique_path_name())
                self._storage.save_pub(cit)

            pub.cit_paths = list(cit_paths)

            self._storage.save_pub(pub)

        # We're trying our best to only advance the stage atomically if stepping completes
        # without errors, however we could be interrupted at any time while saving the data
        # produced by this step to storage. There's not much we can do about this.
        self._stage = self.initial_stage() if step.stage is None else step.stage

        jitter_range = step.delay * DELAY_JITTER_PERCENT
        jitter = random.uniform(-jitter_range, jitter_range)
        self._due = asyncio.get_event_loop().time() + step.delay + jitter

    def remaining_delay(self):
        return self._due - asyncio.get_event_loop().time()

    def due(self):
        return datetime.datetime.now() + datetime.timedelta(
            seconds=self.remaining_delay()
        )

    def __lt__(self, other):
        return self._due < other._due

    def __gt__(self, other):
        return self._due > other._due
