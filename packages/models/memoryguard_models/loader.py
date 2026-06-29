# SPDX-License-Identifier: Apache-2.0
"""``ModelLoader`` -- resolve/serve model artifacts and select model components.

``ModelLoader`` ties the model-layer seams together and is the single place the
core asks for a model. It serves two complementary roles:

1. **Artifact serving** -- given a ``ModelRegistry`` and an ``InferenceRunner``
   it resolves a ``model_id`` (+ optional ``version``) to a ``ModelVersion``,
   loads the artifact via the runner, and serves predictions (``resolve`` /
   ``load`` / ``serve``). With the OSS defaults -- ``LocalFileModelRegistry`` +
   ``LocalInferenceRunner`` -- this path runs entirely on-device with no network
   access.

2. **Component selection by feature flag** -- given a set of ``FeatureFlags`` it
   returns, for each model task (``embed`` / ``rerank`` / ``contradiction`` /
   ``poison`` / ``sensitive`` / ``trust``), the OSS local-default implementation
   while the task's commercial flag is **off**, and a commercial / learned
   implementation only once that flag is **on** (:meth:`get`).

Open-core boundary
------------------
``ModelLoader`` lives in the OSS ``packages/models`` and **never imports a
commercial package**. Both the OSS defaults and any commercial implementations
are supplied to it as zero-argument factories via a registration / injection map
(constructor args or :meth:`register_oss_default` / :meth:`register_commercial`).
This keeps the loader dependency-light and lets the OSS composition root wire the
local defaults while commercial deployments inject their learned models behind
the same model-layer interfaces -- with no change to ``ModelLoader`` or to
``packages/core``.

Loaded artifact handles are cached per resolved ``model_id@version`` and selected
component instances are cached per task; :meth:`clear_cache` drops both.

Requirements: 21.1, 21.5, 27.3, 28.1, 28.2, 28.3.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Callable, Mapping, Optional

from memoryguard_models.base import InferenceRunner, LoadedModel, ModelRegistry
from memoryguard_models.versioning import ModelVersion

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids eager core import)
    from memoryguard_core.flags import FeatureFlags

#: A zero-argument factory that builds a model-component instance on demand.
ModelFactory = Callable[[], object]

#: The set of model tasks the loader can select an implementation for.
MODEL_TASKS: tuple[str, ...] = (
    "embed",
    "rerank",
    "contradiction",
    "poison",
    "sensitive",
    "trust",
)

#: Maps each model task to the ``FeatureFlags`` attribute that, when ``True``,
#: selects the commercial / learned implementation for that task. While the flag
#: is ``False`` the OSS local default is used. These names match the commercial
#: model-layer flags declared in ``memoryguard_core.flags.FeatureFlags``.
TASK_COMMERCIAL_FLAGS: dict[str, str] = {
    "embed": "advanced_embeddings",
    "rerank": "learned_reranker",
    "contradiction": "learned_contradiction",
    "poison": "advanced_poison_detection",
    "sensitive": "advanced_pii_model",
    "trust": "learned_trust_model",
}


class ModelLoader:
    """Resolve/serve models and select OSS-default vs commercial components.

    Args:
        registry: resolves ``model_id``/``version`` to a ``ModelVersion``.
        runner: loads the resolved ``ModelVersion`` and runs inference locally.
        flags: active :class:`FeatureFlags`. When ``None`` (the default), an
            all-OSS flag set is used so every task resolves to its OSS local
            default (every commercial flag is off).
        oss_defaults: optional ``{task: factory}`` map of OSS local-default
            component factories. Each factory takes no arguments and returns a
            ready model instance.
        commercial: optional ``{task: factory}`` map of commercial / learned
            component factories, injected externally. ``ModelLoader`` never
            imports these; it only calls the supplied factories, and only when
            the task's commercial flag is enabled.
        task_flags: optional override of the task -> flag-name mapping (defaults
            to :data:`TASK_COMMERCIAL_FLAGS`).
    """

    def __init__(
        self,
        registry: ModelRegistry,
        runner: InferenceRunner,
        flags: "Optional[FeatureFlags]" = None,
        *,
        oss_defaults: Optional[Mapping[str, ModelFactory]] = None,
        commercial: Optional[Mapping[str, ModelFactory]] = None,
        task_flags: Optional[Mapping[str, str]] = None,
    ) -> None:
        self._registry = registry
        self._runner = runner
        self._flags = flags if flags is not None else _default_flags()
        self._oss_defaults: dict[str, ModelFactory] = dict(oss_defaults or {})
        self._commercial: dict[str, ModelFactory] = dict(commercial or {})
        self._task_flags: dict[str, str] = dict(task_flags or TASK_COMMERCIAL_FLAGS)

        # Caches: loaded artifact handles keyed by ``model_id@version`` and
        # selected component instances keyed by task.
        self._cache: dict[str, LoadedModel] = {}
        self._model_cache: dict[str, object] = {}

    # -- accessors ---------------------------------------------------------

    @property
    def registry(self) -> ModelRegistry:
        """The backing model registry."""

        return self._registry

    @property
    def runner(self) -> InferenceRunner:
        """The active inference runner."""

        return self._runner

    @property
    def flags(self) -> "FeatureFlags":
        """The active feature flags driving component selection."""

        return self._flags

    # -- artifact resolution + loading ------------------------------------

    def resolve(self, model_id: str, version: str | None = None) -> ModelVersion:
        """Resolve ``model_id`` (latest if ``version`` is None) via the registry."""

        return self._registry.resolve(model_id, version)

    def load(self, model_id: str, version: str | None = None) -> LoadedModel:
        """Resolve then load a model, returning a runnable handle.

        The resolved ``model_id@version`` handle is cached; subsequent loads of
        the same resolved version return the cached handle.
        """

        mv = self._registry.resolve(model_id, version)
        key = f"{mv.model_id}@{mv.version}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        loaded = self._runner.load(mv)
        self._cache[key] = loaded
        return loaded

    def serve(
        self,
        model_id: str,
        inputs: list[dict],
        *,
        version: str | None = None,
    ) -> list[dict]:
        """Resolve, load, and run ``model_id`` over ``inputs``.

        Returns the per-input output dicts produced by the inference runner.
        """

        loaded = self.load(model_id, version)
        return self._runner.run(loaded, inputs)

    # -- flag-driven component selection ----------------------------------

    def register_oss_default(self, task: str, factory: ModelFactory) -> None:
        """Register the OSS local-default factory for ``task``.

        Replacing a task's OSS default drops any cached instance for that task.
        """

        self._validate_task(task)
        if not callable(factory):
            raise TypeError("factory must be a zero-argument callable")
        self._oss_defaults[task] = factory
        self._model_cache.pop(task, None)

    def register_commercial(self, task: str, factory: ModelFactory) -> None:
        """Register a commercial / learned factory for ``task`` (injected externally).

        ``ModelLoader`` never imports commercial code; it only invokes the
        supplied factory, and only when ``task``'s commercial flag is enabled.
        Replacing a task's commercial factory drops any cached instance for it.
        """

        self._validate_task(task)
        if not callable(factory):
            raise TypeError("factory must be a zero-argument callable")
        self._commercial[task] = factory
        self._model_cache.pop(task, None)

    def is_commercial_active(self, task: str) -> bool:
        """Return whether ``task`` should resolve to a commercial implementation.

        ``True`` only when the task's commercial flag is on **and** a commercial
        factory has been registered for it; otherwise the OSS default is used.
        """

        self._validate_task(task)
        return self._flag_on(task) and task in self._commercial

    def get(self, task: str) -> object:
        """Return the active model implementation for ``task``.

        Selection rule (Requirements 28.1/28.3 + the per-task commercial flags):

        * while the task's commercial flag is **off**, return the OSS local
          default, and
        * only when the task's commercial flag is **on** *and* a commercial
          factory is registered, return the commercial / learned implementation.

        If the flag is on but no commercial factory is registered, the OSS
        default is returned so the platform stays fully functional. The selected
        instance is cached per task.

        Raises:
            ValueError: if ``task`` is not a known model task.
            LookupError: if no suitable factory is registered for ``task``.
        """

        self._validate_task(task)

        cached = self._model_cache.get(task)
        if cached is not None:
            return cached

        use_commercial = self.is_commercial_active(task)
        factory = self._commercial.get(task) if use_commercial else None
        if factory is None:
            factory = self._oss_defaults.get(task)

        if factory is None:
            kind = "commercial" if self._flag_on(task) else "OSS default"
            raise LookupError(
                f"no {kind} implementation registered for task {task!r}; "
                "register one via register_oss_default()/register_commercial() "
                "or pass it in the loader's oss_defaults/commercial map"
            )

        instance = factory()
        self._model_cache[task] = instance
        return instance

    # -- cache management --------------------------------------------------

    def clear_cache(self) -> None:
        """Drop all cached loaded-artifact handles and selected components."""

        self._cache.clear()
        self._model_cache.clear()

    # -- internals ---------------------------------------------------------

    def _flag_on(self, task: str) -> bool:
        """Return whether ``task``'s commercial flag is enabled."""

        flag_name = self._task_flags.get(task)
        if flag_name is None:
            return False
        return bool(getattr(self._flags, flag_name, False))

    @staticmethod
    def _validate_task(task: str) -> None:
        """Raise ``ValueError`` if ``task`` is not a known model task."""

        if task not in TASK_COMMERCIAL_FLAGS:
            raise ValueError(
                f"unknown model task {task!r}; expected one of {MODEL_TASKS}"
            )


def _default_flags() -> "FeatureFlags":
    """Build an all-OSS ``FeatureFlags`` (every commercial flag off).

    Imported lazily so merely importing this module never eagerly initializes
    ``memoryguard_core`` (keeping ``memoryguard_models`` import-order safe).
    """

    from memoryguard_core.flags import FeatureFlags

    return FeatureFlags()


__all__ = [
    "ModelLoader",
    "ModelFactory",
    "MODEL_TASKS",
    "TASK_COMMERCIAL_FLAGS",
]
