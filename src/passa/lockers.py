# -*- coding=utf-8 -*-

from __future__ import absolute_import, unicode_literals

import itertools

import plette
import requirementslib
import resolvelib
import vistir

from .caches import HashCache
from .hashes import get_hashes
from .metadata import set_metadata
from .providers import BasicProvider, EagerUpgradeProvider, PinReuseProvider
from .reporters import StdOutReporter
from .traces import trace_graph
from .utils import identify_requirment


def _get_requirements(model, section_name):
    """Produce a mapping of identifier: requirement from the section.
    """
    if not model:
        return {}
    return {identify_requirment(r): r for r in (
        requirementslib.Requirement.from_pipfile(name, package._data)
        for name, package in model.get(section_name, {}).items()
    )}


def _iter_derived_entries(state, traces, names):
    """Produce a mapping containing all candidates derived from `names`.

    `name` should provide a collection of requirement identifications from
    a section (i.e. `packages` or `dev-packages`). This function uses `trace`
    to filter out candidates in the state that are present because of an entry
    in that collection.
    """
    if not names:
        return
    names = set(names)
    for name, requirement in state.mapping.items():
        routes = {trace[1] for trace in traces[name] if len(trace) > 1}
        if name not in names and not (names & routes):
            continue
        yield (
            requirement.normalized_name,
            next(iter(requirement.as_pipfile().values()))
        )


class AbstractLocker(object):
    """Helper class to produce a new lock file for a project.

    This is not intended for instantiation. You should use one of its concrete
    subclasses instead. The class contains logic to:

    * Prepare a project for locking
    * Perform the actually resolver invocation
    * Convert resolver output into lock file format
    * Update the project to have the new lock file
    """
    def __init__(self, project):
        self.project = project
        self.default_requirements = _get_requirements(
            project.pipfile, "packages",
        )
        self.develop_requirements = _get_requirements(
            project.pipfile, "dev-packages",
        )

        # This comprehension dance ensures we merge packages from both
        # sections, and definitions in the default section win.
        self.requirements = {k: r for k, r in itertools.chain(
            self.develop_requirements.items(),
            self.default_requirements.items(),
        )}.values()

        self.sources = [s._data.copy() for s in project.pipfile.sources]
        self.allow_prereleases = bool(
            project.pipfile.get("pipenv", {}).get("allow_prereleases", False),
        )

    def __repr__(self):
        return "<{0} @ {1!r}>".format(type(self).__name__, self.project.root)

    def get_provider(self):
        raise NotImplementedError

    def get_reporter(self):
        # TODO: Build SpinnerReporter, and use this only in verbose mode.
        return StdOutReporter(self.requirements)

    def lock(self):
        """Lock specified (abstract) requirements into (concrete) candidates.

        The locking procedure consists of four stages:

        * Resolve versions and dependency graph (powered by ResolveLib).
        * Walk the graph to determine "why" each candidate came to be, i.e.
          what top-level requirements result in a given candidate.
        * Populate hashes for resolved candidates.
        * Populate markers based on dependency specifications of each
          candidate, and the dependency graph.
        """
        provider = self.get_provider()
        reporter = self.get_reporter()
        resolver = resolvelib.Resolver(provider, reporter)

        with vistir.cd(self.project.root):
            state = resolver.resolve(self.requirements)

        traces = trace_graph(state.graph)

        hash_cache = HashCache()
        for r in state.mapping.values():
            if not r.hashes:
                r.hashes = get_hashes(hash_cache, r)

        set_metadata(
            state.mapping, traces,
            provider.fetched_dependencies, provider.requires_pythons,
        )

        lockfile = plette.Lockfile.with_meta_from(self.project.pipfile)
        lockfile["default"] = dict(_iter_derived_entries(
            state, traces, self.default_requirements,
        ))
        lockfile["develop"] = dict(_iter_derived_entries(
            state, traces, self.develop_requirements,
        ))
        self.project.lockfile = lockfile


class BasicLocker(AbstractLocker):
    """Basic concrete locker.

    This takes a project, generates a lock file from its Pipfile, and sets
    the lock file property to the project.
    """
    def get_provider(self):
        return BasicProvider(
            self.requirements, self.sources, self.allow_prereleases,
        )


class PinReuseLocker(AbstractLocker):
    """A specialized locker to handle re-locking based on existing pins.

    See :class:`passa.providers.PinReuseProvider` for more information.
    """
    def __init__(self, project):
        super(PinReuseLocker, self).__init__(project)
        pins = _get_requirements(project.lockfile, "develop")
        pins.update(_get_requirements(project.lockfile, "default"))
        self.preferred_pins = pins

    def get_provider(self):
        return PinReuseProvider(
            self.preferred_pins,
            self.requirements, self.sources, self.allow_prereleases,
        )


class EagerUpgradeLocker(PinReuseLocker):
    """A specialized locker to handle the "eager" upgrade strategy.

    See :class:`passa.providers.EagerUpgradeProvider` for more
    information.
    """
    def __init__(self, tracked_names, *args, **kwargs):
        super(EagerUpgradeLocker, self).__init__(*args, **kwargs)
        self.tracked_names = tracked_names

    def get_provider(self):
        return EagerUpgradeProvider(
            self.tracked_names, self.preferred_pins,
            self.requirements, self.sources, self.allow_prereleases,
        )
