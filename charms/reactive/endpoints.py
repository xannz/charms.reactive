# Copyright 2017 Canonical Limited.
#
# This file is part of charms.reactive.
#
# charms.reactive is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License version 3 as
# published by the Free Software Foundation.
#
# charms.reactive is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with charm-helpers.  If not, see <http://www.gnu.org/licenses/>.

import json
from collections import UserDict
from itertools import chain

from charmhelpers.core import hookenv
from charms.reactive.flags import set_flag, toggle_flag, is_flag_set
from charms.reactive.helpers import data_changed
from charms.reactive.relations import RelationFactory, relation_factory


__all__ = [
    'Endpoint',
]


class Endpoint(RelationFactory):
    """
    New base class for creating interface layers.

    This class is intended to create drop-in, backwards-compatible replacements
    for interface layers previously written using the old
    :class:`~charms.reactive.relations.RelationBase` base class.  With the
    advantages of: having commonly used internal flags managed automatically,
    providing a cleaner, more easily understood pattern for interacting with
    relation data, and being able to use ``@when`` rather than ``@hook`` so
    that interface layers are more similar to charm layers and to remove one
    of the biggest barriers to upgrading from a non-reactive version of a
    charm to a reactive version.

    Four flags are automatically managed for each endpoint. Endpoint handlers
    can react to these flags using the :class:`~charms.reactive.decorators`.

      * ``endpoint.{endpoint_name}.joined`` When the endpoint is :meth:`joined`.
      * ``endpoint.{endpoint_name}.changed`` When any relation data has changed.
      * ``endpoint.{endpoint_name}.changed.{field}`` When a specific field has changed.
      * ``endpoint.{endpoint_name}.departed`` When a remote unit is leaving.

    The ``joined`` flag will be automatically removed if all remote units leave
    all relations, but the others must be manually removed by the interface
    layer.

    These flags should only be used by the decorators of the endpoint handlers.
    While it is possible to use them with any decorators in any layer, these
    flags should be considered internal, private implementation details. It is
    the interface layers responsibility to manage and document the public flags
    that make up part of its API.

    Endpoint handlers can iterate over the list of joined relations for an
    endpoint via the :attr:`~charms.reactive.endpoints.Endpoint.relations`
    collection.
    """

    _endpoints = {}

    @classmethod
    def from_name(cls, endpoint_name):
        """
        Return an Endpoint subclass instance based on the name of the endpoint.
        """
        return cls._endpoints.get(endpoint_name)

    @classmethod
    def from_flag(cls, flag):
        """
        Return an Endpoint subclass instance based on the given flag.

        The instance that is returned depends on the endpoint name embedded
        in the flag.  Flags should be of the form ``endpoint.{name}.extra...``,
        though for legacy purposes, the ``endpoint.`` prefix can be omitted.
        The ``{name}}`` portion will be passed to
        :meth:`~charms.reactive.endpoints.Endpoint.from_name`.

        If an appropriate Endpoint sublcass cannot be found, or the flag name
        can't be parsed, ``None`` will be returned.
        """
        if '.' not in flag:
            return None
        parts = flag.split('.')
        if parts[0] == 'endpoint':
            return cls.from_name(parts[1])
        else:
            # some older handlers might not use the 'endpoint' prefix
            return cls.from_name(parts[0])

    @classmethod
    def _startup(cls):
        """
        Create Endpoint instances and manage automatic flags.
        """
        for endpoint_name in sorted(hookenv.relation_types()):
            # populate context based on attached relations
            relf = relation_factory(endpoint_name)
            if not relf or not issubclass(relf, cls):
                continue

            rids = sorted(hookenv.relation_ids(endpoint_name))
            # ensure that relation IDs have the endpoint name prefix, in case
            # juju decides to drop it at some point
            rids = ['{}:{}'.format(endpoint_name, rid) if ':' not in rid
                    else rid for rid in rids]
            endpoint = relf(endpoint_name, rids)
            cls._endpoints[endpoint_name] = endpoint
            endpoint._manage_flags()
            for relation in endpoint.relations:
                hookenv.atexit(relation._flush_data)

    def __init__(self, endpoint_name, relation_ids=None):
        self._endpoint_name = endpoint_name
        self._relations = KeyList(map(Relation, relation_ids or []),
                                  key='relation_id')
        self._all_units = None

    @property
    def endpoint_name(self):
        """
        Relation name of this endpoint.
        """
        return self._endpoint_name

    @property
    def relations(self):
        """
        Collection of `Relation`s that are established for this `Endpoint`.

        This is a `KeyList`, so it can be iterated and indexed as a list,
        or you can look up relations by their ID.  For example::

            rel0 = endpoint.relations[0]
            assert rel0 is endpoint.relations[rel0.relation_id]
            assert all(rel is endpoint.relations[rel.relation_id]
                       for rel in endpoint.relations)
            print(', '.join(endpoint.relations.keys()))
        """
        return self._relations

    @property
    def joined(self):
        """
        Whether this endpoint has remote applications attached to it.
        """
        return len(self.relations) > 0

    def expand_name(self, flag):
        """
        Complete a flag for this endpoint by expanding the endpoint name.

        If the flag does not already contain ``{endpoint_name}``, it will be
        prefixed with ``endpoint.{endpoint_name}.``. Then, ``str.format`` will
        be used to fill in ``{endpoint_name}`` with ``self.endpoint_name``.
        """
        if '{endpoint_name}' not in flag:
            flag = 'endpoint.{endpoint_name}.' + flag
        return flag.format(endpoint_name=self.endpoint_name)

    def _manage_flags(self):
        """
        Manage automatic relation flags.
        """
        already_joined = is_flag_set(self.expand_name('joined'))
        hook_name = hookenv.hook_name()
        rel_hook = hook_name.startswith(self.endpoint_name + '-relation-')
        departed_hook = rel_hook and hook_name.endswith('-departed')

        toggle_flag(self.expand_name('joined'), self.joined)

        if departed_hook:
            set_flag(self.expand_name('departed'))

        if already_joined and not rel_hook:
            # skip checking relation data outside hooks for this relation
            # to save on API calls to the controller (unless we didn't have
            # the joined flag before, since then we might migrating to Endpoints)
            return

        for unit in self.all_units:
            for key, value in unit.received.items():
                data_key = 'endpoint.{}.{}.{}.{}'.format(self.endpoint_name,
                                                         unit.relation.relation_id,
                                                         unit.unit_name,
                                                         key)
                if data_changed(data_key, value):
                    set_flag(self.expand_name('changed'))
                    set_flag(self.expand_name('changed.{}'.format(key)))

    @property
    def all_units(self):
        """
        A list view of all the units of all relations attached to this
        :class:`~charms.reactive.endpoints.Endpoint`.

        This is actually a
        :class:`~charms.reactive.endpoints.CombinedUnitsView`, so the units
        will be in order by relation ID and then unit name, and you can access a
        merged view of all the units' data as a single mapping.  You should be
        very careful when using the merged data collections, however, and
        consider carefully what will happen when the endpoint has multiple
        relations and multiple remote units on each.  It is probably better to
        iterate over each unit and handle its data individually.  See
        :class:`~charms.reactive.endpoints.CombinedUnitsView` for an
        explanation of how the merged data collections work.

        Note that, because a given application might be related multiple times
        on a given endpoint, units may show up in this collection more than
        once.
        """
        if self._all_units is None:
            units = chain.from_iterable(rel.units for rel in self.relations)
            self._all_units = CombinedUnitsView(units)
        return self._all_units


class Relation:
    def __init__(self, relation_id):
        self._relation_id = relation_id
        self._endpoint_name = relation_id.split(':')[0]
        self._application_name = None
        self._units = None
        self._data = None

    @property
    def relation_id(self):
        """
        This relation's relation ID.
        """
        return self._relation_id

    @property
    def endpoint_name(self):
        """
        This relation's endpoint name.

        This will be the same as the
        :class:`~charms.reactive.endpoints.Endpoint`'s endpoint name.
        """
        return self._endpoint_name

    @property
    def application_name(self):
        """
        The name of the remote application for this relation, or ``None``.

        This is equivalent to::

            relation.units[0].unit_name.split('/')[0]
        """
        if self._application_name is None and self.units:
            self._application_name = self.units[0].unit_name.split('/')[0]
        return self._application_name

    @property
    def units(self):
        """
        A list view of all the units on this relation.

        This is actually a
        :class:`~charms.reactive.endpoints.CombinedUnitsView`, so the units
        will be in order by unit name, and you can access a merged view of all
        of the units' data with ``self.units.received`` and
        ``self.units.received``.  You should be very careful when using the
        merged data collections, however, and consider carefully what will
        happen when there are multiple remote units.  It is probabaly better to
        iterate over each unit and handle its data individually.  See
        :class:`~charms.reactive.endpoints.CombinedUnitsView` for an
        explanation of how the merged data collections work.

        The view can be iterated and indexed as a list, or you can look up units
        by their unit name.  For example::

            by_index = relation.units[0]
            by_name = relation.units['unit/0']
            assert by_index is by_name
            assert all(unit is relation.units[unit.unit_name]
                       for unit in relation.units)
            print(', '.join(relation.units.keys()))
        """
        if self._units is None:
            self._units = CombinedUnitsView([
                RelatedUnit(self, unit_name) for unit_name in
                sorted(hookenv.related_units(self.relation_id))
            ])
        return self._units

    @property
    def to_publish(self):
        """
        This is the relation data that the local unit publishes so it is
        visible to all related units. Use this to communicate with related
        units. It is a writeable
        :class:`~charms.reactive.endpoints.JSONUnitDataView`.

        All values stored in this collection will be automatically JSON
        encoded when they are published. This means that they need to be JSON
        serializable! Mappings stored in this collection will be encoded with
        sorted keys, to ensure that the encoded representation will only change
        if the actual data changes.

        Changes to this data are published at the end of a succesfull hook. The
        data is reset when a hook fails.
        """
        if self._data is None:
            self._data = JSONUnitDataView(
                hookenv.relation_get(unit=hookenv.local_unit(),
                                     rid=self.relation_id),
                writeable=True)
        return self._data

    @property
    def to_publish_raw(self):
        """
        This is the raw relation data that the local unit publishes so it is
        visible to all related units. It is a writeable
        :class:`~charms.reactive.endpoints.UnitDataView`. **Only use this
        for backwards compatibility with interfaces that do not use JSON
        encoding.** Use
        :attr:`~charms.reactive.endpoints.Relation.to_publish` instead.

        Changes to this data are published at the end of a succesfull hook. The
        data is reset when a hook fails.
        """
        return self.to_publish.data

    def _flush_data(self):
        """
        If this relation's local unit data has been modified, publish it on the
        relation. This should be automatically called.
        """
        if self._data and self._data.modified:
            hookenv.relation_set(self.relation_id, dict(self.to_publish.data))


class RelatedUnit:
    """
    Class representing a remote unit on a relation.
    """
    def __init__(self, relation, unit_name):
        self._relation = relation
        self.unit_name = unit_name
        self.application_name = unit_name.split('/')[0]
        self._data = None

    @property
    def relation(self):
        """
        The relation to which this unit belongs.
        """
        return self._relation

    @property
    def received(self):
        """
        A :class:`~charms.reactive.endpoints.JSONUnitDataView` of the data
        received from this remote unit over the relation, with values being
        automatically decoded as JSON.
        """
        if self._data is None:
            self._data = JSONUnitDataView(hookenv.relation_get(
                unit=self.unit_name,
                rid=self.relation.relation_id))
        return self._data

    @property
    def received_raw(self):
        """
        A :class:`~charms.reactive.endpoints.UnitDataView` of the raw data
        received from this remote unit over the relation.
        """
        return self.received.raw_data


class KeyList(list):
    """
    List that also allows accessing items keyed by an attribute on the items.

    Unlike dicts, the keys don't need to be unique.
    """
    def __init__(self, items, key):
        super().__init__(items)
        self._key = key

    def __getitem__(self, key):
        """
        Access an item in this :class:`~charms.reactive.endpoints.KeyList` by
        either an integer index or a str key.

        If an integer key is given, it will be used as a list index.

        If a str is given, it will be used as a mapping key.  Since keys may not
        be unique, only the first item matching the given key will be returned.
        """
        if isinstance(key, int):
            return super().__getitem__(key)
        for item in self:
            if getattr(item, self._key) == key:
                return item
        raise KeyError(key)

    def keys(self):
        """
        Return the keys for all items in this
        :class:`~charms.reactive.endpoints.KeyList`.

        Unlike a dict, the keys are not necessarily unique, so this list may
        contain duplicate values.  The keys will be returned in the order of the
        items in the list.
        """
        return [getattr(item, self._key) for item in self]

    def values(self):
        """
        Return just the values of this list.

        This is equivalent to ``list(keylist)``.
        """
        return list(self)


class CombinedUnitsView(KeyList):
    """
    A :class:`~charms.reactive.endpoints.KeyList` view of
    :class:`~charms.reactive.endpoints.RelatedUnit` items, with properties to
    access a merged view of all of the units' data.

    You can iterate over this view like any other list, or you can look up units
    by their ``unit_name``.  Units will be in order by relation ID and unit
    name.  If a given unit name occurs more than once, accessing it by
    ``unit_name`` will return the one from the lowest relation ID::

        # given the following relations...
        {
            'endpoint:1': {
                'unit/1': {
                    'key0': 'value0_1_1',
                    'key1': 'value1_1_1',
                },
                'unit/0': {
                    'key0': 'value0_1_0',
                    'key1': 'value1_1_0',
                },
            },
            'endpoint:0': {
                'unit/1': {
                    'key0': 'value0_0_1',
                    'key2': 'value2_0_1',
                },
            },
        }

        from_all = endpoint.all_units['unit/1']
        by_rel = endpoint.relations['endpoint:0'].units['unit/1']
        by_index = endpoint.relations[0].units[1]
        assert from_all is by_rel
        assert by_rel is by_index

    You can also use the
    :attr:`~charms.reactive.endpoints.CombinedUnitsView.received` or
    :attr:`~charms.reactive.endpoints.CombinedUnitsView.received_raw`
    properties just like you would on a single unit.  The data in these
    collections will have all of the data from every unit, with units with the
    lowest relation ID and unit name taking precedence if multiple units have
    set a given field.  For example::

        # given the same relations as above...

        # the values across all relations would be:
        assert endpoint.all_units.received['key0'] == 'value0_0_0'
        assert endpoint.all_units.received['key1'] == 'value1_1_0'
        assert endpoint.all_units.received['key2'] == 'value2_0_1'

        # across individual relations:
        assert endpoint.relations[0].units.received['key0'] == 'value0_0_1'
        assert endpoint.relations[0].units.received['key1'] == None
        assert endpoint.relations[0].units.received['key2'] == 'value2_0_1'
        assert endpoint.relations[1].units.received['key0'] == 'value0_1_0'
        assert endpoint.relations[1].units.received['key1'] == 'value1_1_0'
        assert endpoint.relations[1].units.received['key2'] == None

        # and of course you an access them by individual unit
        assert endpoint.relations['endpoint:1'].units['unit/1'].received['key0'] \
                == 'value0_1_1'

    """
    def __init__(self, items):
        super().__init__(sorted(items, key=lambda i: (i.relation.relation_id,
                                                      i.unit_name)),
                         key='unit_name')

    @property
    def received(self):
        """
        Combined :class:`~charms.reactive.endpoints.JSONUnitDataView` of the
        data of all units in this list, with automatic JSON decoding.
        """
        if not hasattr(self, '_data'):
            # NB: units are reversed so that lowest numbered unit takes precedence
            self._data = JSONUnitDataView({key: value
                                           for unit in reversed(self)
                                           for key, value in unit.received_raw.items()})

        return self._data

    @property
    def received_raw(self):
        """
        Combined :class:`~charms.reactive.endpoints.UnitDataView` of the raw data
        of all units in this list, as raw strings.
        """
        return self.received.raw_data


class UnitDataView(UserDict):
    """
    View of a dict containing a unit's data.

    This is like a ``defaultdict(lambda: None)`` which cannot be modified by
    default.
    """
    def __init__(self, data, writeable=False):
        self.data = data
        self._writeable = writeable
        self._modified = False

    @property
    def modified(self):
        """
        Whether this collection has been modified.
        """
        return self._modified

    @property
    def writeable(self):
        """
        Whether this collection can be modified.
        """
        return self._writeable

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __getitem__(self, key):
        return self.data.get(key)

    def __setitem__(self, key, value):
        if not self._writeable:
            raise ValueError('Remote unit data cannot be modified')
        self._modified = True
        self.data[key] = value


class JSONUnitDataView(UserDict):
    """
    View of a dict that performs automatic JSON en/decoding of items.

    Like :class:`~charms.reactive.endpoints.UnitDataView`, this is like a
    ``defaultdict(lambda: None)`` which cannot be modified by default.

    When decoding, if a value fails to decode, it will just return the raw value
    as a string.

    When encoding, it ensures that keys are sorted to maintain stable and
    consistent encoded representations.

    The original data, without automatic encoding / decoding, can be accessed as
    :attr:`raw_data`.
    """
    def __init__(self, data, writeable=False):
        self.data = UnitDataView(data, writeable)

    @property
    def raw_data(self):
        """
        The data for this collection without automatic encoding / decoding.

        This is an :class:`~charms.reactive.endpoints.UnitDataView` instance.
        """
        return self.data

    @property
    def modified(self):
        """
        Whether this collection has been modified.
        """
        return self.raw_data.modified

    @property
    def writeable(self):
        """
        Whether this collection can be modified.
        """
        return self.raw_data.writeable

    def get(self, key, default=None):
        if key not in self.raw_data:
            return default
        return self[key]

    def __getitem__(self, key):
        value = self.raw_data[key]
        if not value:
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    def __setitem__(self, key, value):
        self.raw_data[key] = json.dumps(value, sort_keys=True)


hookenv.atstart(Endpoint._startup)