# -*- coding: utf-8 -*-

"""
Nodenet definition
"""
import json
import os
import copy
import warnings

import theano
from theano import tensor as T
import numpy as np
import scipy.sparse as sp
import scipy

from micropsi_core.nodenet import monitor
from micropsi_core.nodenet.nodenet import Nodenet
from micropsi_core.nodenet.node import Nodetype
from micropsi_core.nodenet.stepoperators import DoernerianEmotionalModulators
from micropsi_core.nodenet.theano_engine.theano_node import *
from micropsi_core.nodenet.theano_engine.theano_definitions import *
from micropsi_core.nodenet.theano_engine.theano_stepoperators import *
from micropsi_core.nodenet.theano_engine.theano_nodespace import *
from micropsi_core.nodenet.theano_engine.theano_netapi import TheanoNetAPI
from micropsi_core.nodenet.theano_engine.theano_section import TheanoSection

from configuration import config as settings


STANDARD_NODETYPES = {
    "Nodespace": {
        "name": "Nodespace"
    },
    "Comment": {
        "name": "Comment",
        "symbol": "#",
        'parameters': ['comment'],
        "shape": "Rectangle"
    },
    "Register": {
        "name": "Register",
        "slottypes": ["gen"],
        "nodefunction_name": "register",
        "gatetypes": ["gen"]
    },
    "Sensor": {
        "name": "Sensor",
        "parameters": ["datasource"],
        "nodefunction_name": "sensor",
        "gatetypes": ["gen"]
    },
    "Actor": {
        "name": "Actor",
        "parameters": ["datatarget"],
        "nodefunction_name": "actor",
        "slottypes": ["gen"],
        "gatetypes": ["gen"]
    },
    "Pipe": {
        "name": "Pipe",
        "slottypes": ["gen", "por", "ret", "sub", "sur", "cat", "exp"],
        "nodefunction_name": "pipe",
        "gatetypes": ["gen", "por", "ret", "sub", "sur", "cat", "exp"],
        "gate_defaults": {
            "gen": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": 0
            },
            "por": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": 0
            },
            "ret": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": 0
            },
            "sub": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": True
            },
            "sur": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": 0
            },
            "cat": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": 1
            },
            "exp": {
                "minimum": -1,
                "maximum": 1,
                "threshold": -1,
                "spreadsheaves": 0
            }
        },
        "parameters": ["expectation", "wait"],
        "parameter_defaults": {
            "expectation": 1,
            "wait": 10
        },
        'symbol': 'πp'
    },
    "Activator": {
        "name": "Activator",
        "slottypes": ["gen"],
        "parameters": ["type"],
        "parameter_values": {"type": ["por", "ret", "sub", "sur", "cat", "exp"]},
        "nodefunction_name": "activator"
    }
}

NODENET_VERSION = 1

class TheanoNodenet(Nodenet):
    """
        theano runtime engine implementation
    """

    @property
    def engine(self):
        return "theano_engine"

    @property
    def current_step(self):
        return self.__step

    @property
    def data(self):
        data = super(TheanoNodenet, self).data
        data['links'] = self.construct_links_dict()
        data['nodes'] = self.construct_nodes_dict()
        # for uid in data['nodes']:
        #    data['nodes'][uid]['gate_parameters'] = self.get_node(uid).clone_non_default_gate_parameters()
        data['nodespaces'] = self.construct_nodespaces_dict(None)
        data['version'] = self.__version
        data['modulators'] = self.construct_modulators_dict()
        return data

    def __init__(self, name="", worldadapter="Default", world=None, owner="", uid=None, native_modules={}):

        # map of string uids to positions. Not all nodes necessarily have an entry.
        self.positions = {}

        # map of string uids to names. Not all nodes neccessarily have an entry.
        self.names = {}

        # map of data sources to numerical node IDs
        self.sensormap = {}

        # map of numerical node IDs to data sources
        self.inverted_sensor_map = {}

        # map of data targets to numerical node IDs
        self.actuatormap = {}

        # map of numerical node IDs to data targets
        self.inverted_actuator_map = {}

        super(TheanoNodenet, self).__init__(name, worldadapter, world, owner, uid)

        INITIAL_NUMBER_OF_NODESPACES = 10

        AVERAGE_ELEMENTS_PER_NODE_ASSUMPTION = 4
        configured_elements_per_node_assumption = settings['theano']['elements_per_node_assumption']
        try:
            AVERAGE_ELEMENTS_PER_NODE_ASSUMPTION = int(configured_elements_per_node_assumption)
        except:
            self.logger.warn("Unsupported elements_per_node_assumption value from configuration: %s, falling back to 4", configured_elements_per_node_assumption)

        INITIAL_NUMBER_OF_NODES = 2000
        configured_initial_number_of_nodes = settings['theano']['initial_number_of_nodes']
        try:
            INITIAL_NUMBER_OF_NODES = int(configured_initial_number_of_nodes)
        except:
            self.logger.warn("Unsupported initial_number_of_nodes value from configuration: %s, falling back to 2000", configured_initial_number_of_nodes)

        INITIAL_NUMBER_OF_ELEMENTS = INITIAL_NUMBER_OF_NODES * AVERAGE_ELEMENTS_PER_NODE_ASSUMPTION

        sparse = True
        configuredsparse = settings['theano']['sparse_weight_matrix']
        if configuredsparse == "True":
            sparse = True
        elif configuredsparse == "False":
            sparse = False
        else:
            self.logger.warn("Unsupported sparse_weight_matrix value from configuration: %s, falling back to True", configuredsparse)
            sparse = True

        precision = settings['theano']['precision']
        if precision == "32":
            T.config.floatX = "float32"
            self.scipyfloatX = scipy.float32
            self.numpyfloatX = np.float32
            self.byte_per_float = 4
        elif precision == "64":
            T.config.floatX = "float64"
            self.scipyfloatX = scipy.float64
            self.numpyfloatX = np.float64
            self.byte_per_float = 8
        else:
            self.logger.warn("Unsupported precision value from configuration: %s, falling back to float64", precision)
            T.config.floatX = "float64"
            self.scipyfloatX = scipy.float64
            self.numpyfloatX = np.float64
            self.byte_per_float = 8

        device = T.config.device
        self.logger.info("Theano configured to use %s", device)
        if device.startswith("gpu"):
            self.logger.info("Using CUDA with cuda_root=%s and theano_flags=%s", os.environ["CUDA_ROOT"], os.environ["THEANO_FLAGS"])
            if T.config.floatX != "float32":
                self.logger.warn("Precision set to %s, but attempting to use gpu.", precision)

        self.netapi = TheanoNetAPI(self)

        self.sections = {}
        rootsection = TheanoSection(self,
                                         0,
                                         sparse,
                                         INITIAL_NUMBER_OF_NODES,
                                         INITIAL_NUMBER_OF_ELEMENTS,
                                         INITIAL_NUMBER_OF_NODESPACES)

        self.sections['root'] = rootsection
        self.rootsection = rootsection

        self.__version = NODENET_VERSION  # used to check compatibility of the node net data
        self.__step = 0
        self.__modulators = {}
        self.__modulators['por_ret_decay'] = 0.

        self.proxycache = {}

        self.stepoperators = []
        self.initialize_stepoperators()

        self.__nodetypes = {}
        for type, data in STANDARD_NODETYPES.items():
            self.__nodetypes[type] = Nodetype(nodenet=self, **data)

        self.native_module_definitions = native_modules
        self.native_modules = {}
        for type, data in self.native_module_definitions.items():
            self.native_modules[type] = Nodetype(nodenet=self, **data)

        self.create_nodespace(None, None, "Root", nodespace_to_id(1))

        self.initialize_nodenet({})

    def initialize_stepoperators(self):
        self.stepoperators = [
            TheanoPropagate(),
            TheanoCalculate(self),
            TheanoPORRETDecay(),
            DoernerianEmotionalModulators()]
        self.stepoperators.sort(key=lambda op: op.priority)

    def save(self, filename):

        # write json metadata, which will be used by runtime to manage the net
        with open(filename, 'w+') as fp:
            metadata = self.metadata
            metadata['positions'] = self.positions
            metadata['names'] = self.names
            metadata['actuatormap'] = self.actuatormap
            metadata['sensormap'] = self.sensormap
            metadata['nodes'] = self.construct_native_modules_and_comments_dict()
            metadata['monitors'] = self.construct_monitors_dict()
            metadata['modulators'] = self.construct_modulators_dict()
            fp.write(json.dumps(metadata, sort_keys=True, indent=4))

        for section in self.sections.values():
            sid = "%03i" % section.sid
            # write bulk data to our own numpy-based file format
            datafilename = os.path.join(os.path.dirname(filename), self.uid + "-data-" + sid)
            section.save(datafilename)

    def load(self, filename):
        """Load the node net from a file"""
        # try to access file

        with self.netlock:
            initfrom = {}
            datafile = None
            if os.path.isfile(filename):
                try:
                    self.logger.info("Loading nodenet %s metadata from file %s", self.name, filename)
                    with open(filename) as file:
                        initfrom.update(json.load(file))
                except ValueError:
                    warnings.warn("Could not read nodenet metadata from file %s", filename)
                    return False
                except IOError:
                    warnings.warn("Could not open nodenet metadata file %s", filename)
                    return False

            # initialize with metadata
            self.initialize_nodenet(initfrom)

            nodes_data = {}
            if 'nodes' in initfrom:
                nodes_data = initfrom['nodes']

            for section in self.sections.values():
                sid = "%03i" % section.sid
                datafilename = os.path.join(os.path.dirname(filename), self.uid + "-data-" + sid + ".npz")
                section.load(datafilename, nodes_data)

            # reloading native modules ensures the types in allocated_nodes are up to date
            # (numerical native module types are runtime dependent and may differ from when allocated_nodes
            # was saved).
            self.reload_native_modules(self.native_module_definitions)

            for sensor, id_list in self.sensormap.items():
                for id in id_list:
                    self.inverted_sensor_map[node_to_id(id, self.rootsection.sid)] = sensor
            for actuator, id_list in self.actuatormap.items():
                for id in id_list:
                    self.inverted_actuator_map[node_to_id(id, self.rootsection.sid)] = actuator

            # re-initialize step operators for theano recompile to new shared variables
            self.initialize_stepoperators()

            return True

    def remove(self, filename):
        neighbors = os.listdir(os.path.dirname(filename))
        for neighbor in neighbors:
            if neighbor.startswith(self.uid):
                os.remove(os.path.join(os.path.dirname(filename), neighbor))

    def initialize_nodenet(self, initfrom):

        self.__modulators.update(initfrom.get("modulators", {}))

        if len(initfrom) != 0:
            # now merge in all init data (from the persisted file typically)
            self.merge_data(initfrom, keep_uids=True)
            if 'names' in initfrom:
                self.names = initfrom['names']
            if 'positions' in initfrom:
                self.positions = initfrom['positions']
            if 'actuatormap' in initfrom:
                self.actuatormap = initfrom['actuatormap']
            if 'sensormap' in initfrom:
                self.sensormap = initfrom['sensormap']
            if 'current_step' in initfrom:
                self.__step = initfrom['current_step']

    def merge_data(self, nodenet_data, keep_uids=False):
        """merges the nodenet state with the current node net, might have to give new UIDs to some entities"""

        uidmap = {}
        # for dict_engine compatibility
        uidmap["Root"] = "s1"

        # re-use the root nodespace
        uidmap["s1"] = "s1"

        # merge in spaces, make sure that parent nodespaces exist before children are initialized
        nodespaces_to_merge = set(nodenet_data.get('nodespaces', {}).keys())
        for nodespace in nodespaces_to_merge:
            self.merge_nodespace_data(nodespace, nodenet_data['nodespaces'], uidmap, keep_uids)

        # merge in nodes
        for uid in nodenet_data.get('nodes', {}):
            data = nodenet_data['nodes'][uid]
            parent_uid = data['parent_nodespace']
            if not keep_uids:
                parent_uid = uidmap[data['parent_nodespace']]
            if data['type'] in self.__nodetypes or data['type'] in self.native_modules:
                olduid = None
                if keep_uids:
                    olduid = uid
                new_uid = self.create_node(
                    data['type'],
                    parent_uid,
                    data['position'],
                    name=data['name'],
                    uid=olduid,
                    parameters=data.get('parameters'),
                    gate_parameters=data.get('gate_parameters'),
                    gate_functions=data.get('gate_functions'))
                uidmap[uid] = new_uid
                node_proxy = self.get_node(new_uid)
                for gatetype in data.get('gate_activations', {}):   # todo: implement sheaves
                    if gatetype in node_proxy.nodetype.gatetypes:
                        node_proxy.get_gate(gatetype).activation = data['gate_activations'][gatetype]['default']['activation']
                state = data.get('state', {})
                if state is not None:
                    for key, value in state.items():
                        node_proxy.set_state(key, value)

            else:
                warnings.warn("Invalid nodetype %s for node %s" % (data['type'], uid))

        # merge in links
        for linkid in nodenet_data.get('links', {}):
            data = nodenet_data['links'][linkid]
            self.create_link(
                uidmap[data['source_node_uid']],
                data['source_gate_name'],
                uidmap[data['target_node_uid']],
                data['target_slot_name'],
                data['weight']
            )

        for monitorid in nodenet_data.get('monitors', {}):
            data = nodenet_data['monitors'][monitorid]
            if 'node_uid' in data:
                old_node_uid = data['node_uid']
                if old_node_uid in uidmap:
                    data['node_uid'] = uidmap[old_node_uid]
            if 'classname' in data:
                if hasattr(monitor, data['classname']):
                    getattr(monitor, data['classname'])(self, **data)
                else:
                    self.logger.warn('unknown classname for monitor: %s (uid:%s) ' % (data['classname'], monitorid))
            else:
                # Compatibility mode
                monitor.NodeMonitor(self, name=data['node_name'], **data)

    def merge_nodespace_data(self, nodespace_uid, data, uidmap, keep_uids=False):
        """
        merges the given nodespace with the given nodespace data dict
        This will make sure all parent nodespaces for the given nodespace exist (and create the parents
        if necessary)
        """
        if keep_uids:
            section = self.get_section(nodespace_uid)
            id = nodespace_from_id(nodespace_uid)
            if section.allocated_nodespaces[id] == 0:
                # move up the nodespace tree until we find an existing parent or hit root
                if id != 1:
                    parent_id = nodespace_from_id(data[nodespace_uid].get('parent_nodespace'))
                    if section.allocated_nodespaces[parent_id] == 0:
                        self.merge_nodespace_data(nodespace_to_id(parent_id), data, uidmap, keep_uids)
                self.create_nodespace(
                    data[nodespace_uid].get('parent_nodespace'),
                    data[nodespace_uid].get('position'),
                    name=data[nodespace_uid].get('name', 'Root'),
                    uid=nodespace_uid
                )
        else:
            if not nodespace_uid in uidmap:
                parent_uid = data[nodespace_uid].get('parent_nodespace')
                if not parent_uid in uidmap:
                    self.merge_nodespace_data(parent_uid, data, uidmap, keep_uids)
                newuid = self.create_nodespace(
                    uidmap[data[nodespace_uid].get('parent_nodespace')],
                    data[nodespace_uid].get('position'),
                    name=data[nodespace_uid].get('name', 'Root'),
                    uid=None
                )
                uidmap[nodespace_uid] = newuid

    def step(self):
        self.user_prompt = None
        if self.world is not None and self.world.agents is not None and self.uid in self.world.agents:
            self.world.agents[self.uid].snapshot()      # world adapter snapshot
                                                        # TODO: Not really sure why we don't just know our world adapter,
                                                        # but instead the world object itself

        with self.netlock:
            for operator in self.stepoperators:
                operator.execute(self, None, self.netapi)

            self.__step += 1

    def get_section(self, uid):
        return self.rootsection

    def get_node(self, uid):
        section = self.get_section(uid)
        if uid in section.native_module_instances:
            return section.native_module_instances[uid]
        elif uid in section.comment_instances:
            return section.comment_instances[uid]
        elif uid in self.proxycache:
            return self.proxycache[uid]
        elif self.is_node(uid):
            id = node_from_id(uid)
            parent_id = section.allocated_node_parents[id]
            node = TheanoNode(self, section, nodespace_to_id(parent_id), uid, section.allocated_nodes[id])
            self.proxycache[node.uid] = node
            return node
        else:
            raise KeyError("No node with id %s exists", uid)

    def get_node_uids(self, group_nodespace_uid=None, group=None):
        if group is not None:
            if group_nodespace_uid is None:
                group_nodespace_uid = self.get_nodespace(None).uid
            section = self.get_section(group_nodespace_uid)
            return [node_to_id(nid, section.sid) for nid in section.allocated_elements_to_nodes[section.nodegroups[group_nodespace_uid][group]]]
        else:
            uids = []
            for section in self.sections.values():
                uids.extend([node_to_id(id, section.sid) for id in np.nonzero(section.allocated_nodes)[0]])
            return uids

    def is_node(self, uid):
        section = self.get_section(uid)
        numid = node_from_id(uid)
        return numid < section.NoN and section.allocated_nodes[numid] != 0

    def announce_nodes(self, nodespace_uid, number_of_nodes, average_elements_per_node):
        section = self.get_section(nodespace_uid)
        section.announce_nodes(number_of_nodes, average_elements_per_node)

    def create_node(self, nodetype, nodespace_uid, position, name=None, uid=None, parameters=None, gate_parameters=None, gate_functions=None):
        nodespace_uid = self.get_nodespace(nodespace_uid).uid
        section = self.get_section(nodespace_uid)
        nodespace_id = nodespace_from_id(nodespace_uid)

        id_to_pass = None
        if uid is not None:
            id_to_pass = node_from_id(uid)

        id = section.create_node(nodetype, nodespace_id, id_to_pass, parameters, gate_parameters, gate_functions)
        uid = node_to_id(id, section.sid)

        if position is not None:
            self.positions[uid] = position
        if name is not None and name != "" and name != uid:
            self.names[uid] = name

        if parameters is None:
            parameters = {}

        if nodetype == "Sensor":
            if 'datasource' in parameters:
                datasource = parameters['datasource']
                if datasource is not None:
                    connectedsensors = self.sensormap.get(datasource, [])
                    connectedsensors.append(id)
                    self.sensormap[datasource] = connectedsensors
                    self.inverted_sensor_map[uid] = datasource
        elif nodetype == "Actor":
            if 'datatarget' in parameters:
                datatarget = parameters['datatarget']
                if datatarget is not None:
                    connectedactuators = self.actuatormap.get(datatarget, [])
                    connectedactuators.append(id)
                    self.actuatormap[datatarget] = connectedactuators
                    self.inverted_actuator_map[uid] = datatarget

        return uid

    def delete_node(self, uid):
        section = self.get_section(uid)
        node_id = node_from_id(uid)

        section.delete_node(node_id)

        # remove sensor association if there should be one
        if uid in self.inverted_sensor_map:
            sensor = self.inverted_sensor_map[uid]
            del self.inverted_sensor_map[uid]
            if sensor in self.sensormap:
                self.sensormap[sensor].remove(node_id)
                if len(self.sensormap[sensor]) == 0:
                    del self.sensormap[sensor]

        # remove actuator association if there should be one
        if uid in self.inverted_actuator_map:
            actuator = self.inverted_actuator_map[uid]
            del self.inverted_actuator_map[uid]
            if actuator in self.actuatormap:
                self.actuatormap[actuator].remove(node_id)
                if len(self.actuatormap[actuator]) == 0:
                    del self.actuatormap[actuator]

        self.clear_supplements(uid)

    def set_node_gate_parameter(self, uid, gate_type, parameter, value):
        section = self.get_section(uid)
        id = node_from_id(uid)
        section.set_node_gate_parameter(id, gate_type, parameter, value)

    def set_node_gatefunction_name(self, uid, gate_type, gatefunction_name):
        section = self.get_section(uid)
        id = node_from_id(uid)
        section.set_node_gatefunction_name(id, gate_type, gatefunction_name)

    def set_nodespace_gatetype_activator(self, nodespace_uid, gate_type, activator_uid):
        section = self.get_section(nodespace_uid)
        activator_id = 0
        if activator_uid is not None and len(activator_uid) > 0:
            activator_id = node_from_id(activator_uid)
        nodespace_id = nodespace_from_id(nodespace_uid)
        section.set_nodespace_gatetype_activator(nodespace_id, gate_type, activator_id)

    def get_nodespace(self, uid):
        if uid is None:
            uid = nodespace_to_id(1)

        section = self.get_section(uid)

        if uid in self.proxycache:
            return self.proxycache[uid]
        else:
            nodespace = TheanoNodespace(self, section, uid)
            self.proxycache[uid] = nodespace
            return nodespace

    def get_nodespace_uids(self):
        ids = []
        for section in self.sections.values():
            ids.extend([nodespace_to_id(id) for id in np.nonzero(section.allocated_nodespaces)[0]])
        ids.append(nodespace_to_id(1))
        return ids

    def is_nodespace(self, uid):
        return uid in self.get_nodespace_uids()

    def create_nodespace(self, parent_uid, position, name="", uid=None):
        section = self.get_section(parent_uid)

        parent_id = 0
        if parent_uid is not None:
            parent_id = nodespace_from_id(parent_uid)
        elif uid != "s1":
            parent_id = 1

        id_to_pass = None
        if uid is not None:
            id_to_pass = nodespace_from_id(uid)

        id = section.create_nodespace(parent_id, id_to_pass)
        uid = nodespace_to_id(id)
        if name is not None and len(name) > 0 and name != uid:
            self.names[uid] = name
        if position is not None:
            self.positions[uid] = position

        return uid

    def delete_nodespace(self, nodespace_uid):
        section = self.get_section(nodespace_uid)

        if nodespace_uid is None or nodespace_uid == self.get_nodespace(None).uid:
            raise ValueError("The root nodespace cannot be deleted.")
        nodespace_id = nodespace_from_id(nodespace_uid)
        section.delete_nodespace(nodespace_id)

    def clear_supplements(self, uid):
        # clear from proxycache
        if uid in self.proxycache:
            del self.proxycache[uid]

        # clear from name and positions dicts
        if uid in self.names:
            del self.names[uid]
        if uid in self.positions:
            del self.positions[uid]

    def get_sensors(self, nodespace=None, datasource=None):
        sensors = {}
        sensorlist = []
        if datasource is None:
            for ds_sensors in self.sensormap.values():
                sensorlist.extend(ds_sensors)
        elif datasource in self.sensormap:
            sensorlist = self.sensormap[datasource]
        for id in sensorlist:
            if nodespace is None or self.rootsection.allocated_node_parents[id] == nodespace_from_id(nodespace):
                uid = node_to_id(id, self.rootsection.sid)
                sensors[uid] = self.get_node(uid)
        return sensors

    def get_actors(self, nodespace=None, datatarget=None):
        actuators = {}
        actuatorlist = []
        if datatarget is None:
            for dt_actuators in self.actuatormap.values():
                actuatorlist.extend(dt_actuators)
        elif datatarget in self.actuatormap:
            actuatorlist = self.actuatormap[datatarget]
        for id in actuatorlist:
            if nodespace is None or self.rootsection.allocated_node_parents[id] == nodespace_from_id(nodespace):
                uid = node_to_id(id, self.rootsection.sid)
                actuators[uid] = self.get_node(uid)
        return actuators

    def create_link(self, source_node_uid, gate_type, target_node_uid, slot_type, weight=1, certainty=1):
        return self.set_link_weight(source_node_uid, gate_type, target_node_uid, slot_type, weight)

    def set_link_weight(self, source_node_uid, gate_type, target_node_uid, slot_type, weight=1, certainty=1):

        source_section = self.get_section(source_node_uid)
        target_section = self.get_section(target_node_uid)

        if target_section != source_section:
            raise ValueError("Links between sections aren't supported yet, but will be")

        source_node_id = node_from_id(source_node_uid)
        target_node_id = node_from_id(target_node_uid)

        source_section.set_link_weight(source_node_id, gate_type, target_node_id, slot_type, weight)

        if source_node_uid in self.proxycache:
            self.proxycache[source_node_uid].get_gate(gate_type).invalidate_caches()
        if target_node_uid in self.proxycache:
            self.proxycache[target_node_uid].get_slot(slot_type).invalidate_caches()

        return True

    def delete_link(self, source_node_uid, gate_type, target_node_uid, slot_type):
        return self.set_link_weight(source_node_uid, gate_type, target_node_uid, slot_type, 0)

    def reload_native_modules(self, native_modules):

        self.native_module_definitions = native_modules

        # check which instances need to be recreated because of gate/slot changes and keep their .data
        instances_to_recreate = {}
        instances_to_delete = {}
        for section in self.sections.values():
            for uid, instance in section.native_module_instances.items():
                if instance.type not in native_modules:
                    self.logger.warn("No more definition available for node type %s, deleting instance %s" %
                                    (instance.type, uid))
                    instances_to_delete[uid] = instance
                    continue

                numeric_id = node_from_id(uid)
                number_of_elements = len(np.where(section.allocated_elements_to_nodes == numeric_id)[0])
                new_numer_of_elements = max(len(native_modules[instance.type]['slottypes']), len(native_modules[instance.type]['gatetypes']))
                if number_of_elements != new_numer_of_elements:
                    self.logger.warn("Number of elements changed for node type %s from %d to %d, recreating instance %s" %
                                    (instance.type, number_of_elements, new_numer_of_elements, uid))
                    instances_to_recreate[uid] = instance.data

            # actually remove the instances
            for uid in instances_to_delete.keys():
                self.delete_node(uid)
            for uid in instances_to_recreate.keys():
                self.delete_node(uid)

            # update the node functions of all Nodetypes
            self.native_modules = {}
            for type, data in native_modules.items():
                self.native_modules[type] = Nodetype(nodenet=self, **native_modules[type])

            # update the living instances that have the same slot/gate numbers
            new_instances = {}
            for id, instance in section.native_module_instances.items():
                parameters = instance.clone_parameters()
                state = instance.clone_state()
                position = instance.position
                name = instance.name
                new_native_module_instance = TheanoNode(self, instance.parent_nodespace, id, section.allocated_nodes[node_from_id(id)])
                new_native_module_instance.position = position
                new_native_module_instance.name = name
                for key, value in parameters.items():
                    new_native_module_instance.set_parameter(key, value)
                for key, value in state.items():
                    new_native_module_instance.set_state(key, value)
                new_instances[id] = new_native_module_instance
            section.native_module_instances = new_instances

            # recreate the deleted ones. Gate configurations and links will not be transferred.
            for uid, data in instances_to_recreate.items():
                new_uid = self.create_node(
                    data['type'],
                    data['parent_nodespace'],
                    data['position'],
                    name=data['name'],
                    uid=uid,
                    parameters=data['parameters'])

            # update native modules numeric types, as these may have been set with a different native module
            # node types list
            native_module_ids = np.where(section.allocated_nodes > MAX_STD_NODETYPE)[0]
            for id in native_module_ids:
                instance = self.get_node(node_to_id(id, section.sid))
                section.allocated_nodes[id] = get_numerical_node_type(instance.type, self.native_modules)

    def get_nodespace_data(self, nodespace_uid, include_links):
        section = self.get_section(nodespace_uid)
        data = {
            'links': {},
            'nodes': self.construct_nodes_dict(nodespace_uid, 1000),
            'nodespaces': self.construct_nodespaces_dict(nodespace_uid),
            'monitors': self.construct_monitors_dict(),
            'modulators': self.construct_modulators_dict()
        }
        if include_links:
            data['links'] = self.construct_links_dict(nodespace_uid)

            followupnodes = []
            for uid in data['nodes']:
                followupnodes.extend(self.get_node(uid).get_associated_node_uids())

            for uid in followupnodes:
                if section.allocated_node_parents[node_from_id(uid)] != nodespace_from_id(nodespace_uid):
                    data['nodes'][uid] = self.get_node(uid).data

        if self.user_prompt is not None:
            data['user_prompt'] = self.user_prompt.copy()
            self.user_prompt = None
        return data

    def get_modulator(self, modulator):
        return self.__modulators.get(modulator, 1)

    def change_modulator(self, modulator, diff):
        self.__modulators[modulator] = self.__modulators.get(modulator, 0) + diff

    def set_modulator(self, modulator, value):
        self.__modulators[modulator] = value

    def get_nodetype(self, type):
        if type in self.__nodetypes:
            return self.__nodetypes[type]
        else:
            return self.native_modules.get(type)

    def construct_links_dict(self, nodespace_uid=None):
        data = {}

        for section in self.sections.values():
            if nodespace_uid is not None:
                nssection = self.get_section(nodespace_uid)
                if nssection != section:
                    continue
                parent = nodespace_from_id(nodespace_uid)
                node_ids = np.where(section.allocated_node_parents == parent)[0]
            else:
                node_ids = np.nonzero(section.allocated_nodes)[0]
            w_matrix = section.w.get_value(borrow=True)
            for node_id in node_ids:

                source_type = section.allocated_nodes[node_id]
                for gate_type in range(get_gates_per_type(source_type, self.native_modules)):
                    gatecolumn = w_matrix[:, section.allocated_node_offsets[node_id] + gate_type]
                    links_indices = np.nonzero(gatecolumn)[0]
                    for index in links_indices:
                        target_id = section.allocated_elements_to_nodes[index]
                        target_type = section.allocated_nodes[target_id]
                        target_slot_numerical = index - section.allocated_node_offsets[target_id]
                        target_slot_type = get_string_slot_type(target_slot_numerical, self.get_nodetype(get_string_node_type(target_type, self.native_modules)))
                        source_gate_type = get_string_gate_type(gate_type, self.get_nodetype(get_string_node_type(source_type, self.native_modules)))
                        if section.sparse:               # sparse matrices return matrices of dimension (1,1) as values
                            weight = float(gatecolumn[index].data)
                        else:
                            weight = gatecolumn[index].item()

                        linkuid = "%s:%s:%s:%s" % (node_to_id(node_id, section.sid), source_gate_type, target_slot_type, node_to_id(target_id, section.sid))
                        linkdata = {
                            "uid": linkuid,
                            "weight": weight,
                            "certainty": 1,
                            "source_gate_name": source_gate_type,
                            "source_node_uid": node_to_id(node_id, section.sid),
                            "target_slot_name": target_slot_type,
                            "target_node_uid": node_to_id(target_id, section.sid)
                        }
                        data[linkuid] = linkdata

                target_type = section.allocated_nodes[node_id]
                for slot_type in range(get_slots_per_type(target_type, self.native_modules)):
                    slotrow = w_matrix[section.allocated_node_offsets[node_id] + slot_type]
                    if section.sparse:
                        links_indices = np.nonzero(slotrow)[1]
                    else:
                        links_indices = np.nonzero(slotrow)[0]
                    for index in links_indices:
                        source_id = section.allocated_elements_to_nodes[index]
                        source_type = section.allocated_nodes[source_id]
                        source_gate_numerical = index - section.allocated_node_offsets[source_id]
                        source_gate_type = get_string_gate_type(source_gate_numerical, self.get_nodetype(get_string_node_type(source_type, self.native_modules)))
                        target_slot_type = get_string_slot_type(slot_type, self.get_nodetype(get_string_node_type(target_type, self.native_modules)))
                        if section.sparse:
                            weight = float(slotrow[0, index])
                        else:
                            weight = slotrow[index].item()

                        linkuid = "%s:%s:%s:%s" % (node_to_id(source_id, section.sid), source_gate_type, target_slot_type, node_to_id(node_id, section.sid))
                        linkdata = {
                            "uid": linkuid,
                            "weight": weight,
                            "certainty": 1,
                            "source_gate_name": source_gate_type,
                            "source_node_uid": node_to_id(source_id, section.sid),
                            "target_slot_name": target_slot_type,
                            "target_node_uid": node_to_id(node_id, section.sid)
                        }
                        data[linkuid] = linkdata

        return data

    def construct_native_modules_and_comments_dict(self):
        data = {}
        i = 0
        for section in self.sections.values():
            nodeids = np.where((section.allocated_nodes > MAX_STD_NODETYPE) | (section.allocated_nodes == COMMENT))[0]
            for node_id in nodeids:
                i += 1
                node_uid = node_to_id(node_id, section.sid)
                data[node_uid] = self.get_node(node_uid).data
        return data

    def construct_nodes_dict(self, nodespace_uid=None, max_nodes=-1):
        data = {}
        i = 0
        for section in self.sections.values():
            nodeids = np.nonzero(section.allocated_nodes)[0]
            if nodespace_uid is not None:
                parent_id = nodespace_from_id(nodespace_uid)
                nodeids = np.where(section.allocated_node_parents == parent_id)[0]
            for node_id in nodeids:
                i += 1
                node_uid = node_to_id(node_id, section.sid)
                data[node_uid] = self.get_node(node_uid).data
                if max_nodes > 0 and i > max_nodes:
                    break
        return data

    def construct_nodespaces_dict(self, nodespace_uid):
        data = {}
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid

        for section in self.sections.values():
            nodespace_id = nodespace_from_id(nodespace_uid)
            nodespace_ids = np.nonzero(section.allocated_nodespaces)[0]
            nodespace_ids = np.append(nodespace_ids, 1)
            for candidate_id in nodespace_ids:
                is_in_hierarchy = False
                if candidate_id == nodespace_id:
                    is_in_hierarchy = True
                else:
                    parent_id = section.allocated_nodespaces[candidate_id]
                    while parent_id > 0 and parent_id != nodespace_id:
                        parent_id = section.allocated_nodespaces[parent_id]
                    if parent_id == nodespace_id:
                        is_in_hierarchy = True

                if is_in_hierarchy:
                    data[nodespace_to_id(candidate_id)] = self.get_nodespace(nodespace_to_id(candidate_id)).data

        return data

    def construct_modulators_dict(self):
        return self.__modulators.copy()

    def get_standard_nodetype_definitions(self):
        """
        Returns the standard node types supported by this nodenet
        """
        return copy.deepcopy(STANDARD_NODETYPES)

    def set_sensors_and_actuator_feedback_to_values(self, datasource_to_value_map, datatarget_to_value_map):
        """
        Sets the sensors for the given data sources to the given values
        """

        a_array = self.rootsection.a.get_value(borrow=True)

        for datasource in datasource_to_value_map:
            value = datasource_to_value_map.get(datasource)
            sensor_uids = self.sensormap.get(datasource, [])

            for sensor_uid in sensor_uids:
                a_array[self.rootsection.allocated_node_offsets[sensor_uid] + GEN] = value

        for datatarget in datatarget_to_value_map:
            value = datatarget_to_value_map.get(datatarget)
            actuator_uids = self.actuatormap.get(datatarget, [])

            for actuator_uid in actuator_uids:
                a_array[self.rootsection.allocated_node_offsets[actuator_uid] + GEN] = value

        self.rootsection.a.set_value(a_array, borrow=True)

    def read_actuators(self):
        """
        Returns a map of datatargets to values for writing back to the world adapter
        """

        actuator_values_to_write = {}

        a_array = self.rootsection.a.get_value(borrow=True)

        for datatarget in self.actuatormap:
            actuator_node_activations = 0
            for actuator_id in self.actuatormap[datatarget]:
                actuator_node_activations += a_array[self.rootsection.allocated_node_offsets[actuator_id] + GEN]

            actuator_values_to_write[datatarget] = actuator_node_activations

        self.rootsection.a.set_value(a_array, borrow=True)

        return actuator_values_to_write

    def group_nodes_by_names(self, nodespace_uid, node_name_prefix=None, gatetype="gen", sortby='id'):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid

        ids = []
        for uid, name in self.names.items():
            section = self.get_node(uid)
            if name.startswith(node_name_prefix) and \
                    (section.allocated_node_parents[node_from_id(uid)] == nodespace_from_id(nodespace_uid)):
                ids.append(uid)
        self.group_nodes_by_ids(nodespace_uid, ids, node_name_prefix, gatetype, sortby)

    def group_nodes_by_ids(self, nodespace_uid, node_uids, group_name, gatetype="gen", sortby='id'):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)

        ids = [node_from_id(uid) for uid in node_uids]
        if sortby == 'id':
            ids = sorted(ids)
        elif sortby == 'name':
            ids = sorted(ids, key=lambda id: self.names[node_to_id(id, section.sid)])

        section.group_nodes_by_ids(nodespace_uid, ids, group_name, gatetype)

    def ungroup_nodes(self, nodespace_uid, group):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)
        section.ungroup_nodes(nodespace_uid, group)

    def dump_group(self, nodespace_uid, group):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)

        ids = section.nodegroups[nodespace_uid][group]
        for element in ids:
            nid = section.allocated_elements_to_nodes[element]
            uid = node_to_id(nid, section.sid)
            node = self.get_node(uid)
            print("%s %s" % (node.uid, node.name))

    def get_activations(self, nodespace_uid, group):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)
        return section.get_activations(nodespace_uid, group)

    def set_activations(self, nodespace_uid, group, new_activations):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)
        section.set_activations(nodespace_uid, group, new_activations)

    def get_thetas(self, nodespace_uid, group):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)
        return section.get_thetas(nodespace_uid, group)

    def set_thetas(self, nodespace_uid, group, new_thetas):
        if nodespace_uid is None:
            nodespace_uid = self.get_nodespace(None).uid
        section = self.get_section(nodespace_uid)
        section.set_thetas(nodespace_uid, group, new_thetas)

    def get_link_weights(self, nodespace_from_uid, group_from, nodespace_to_uid, group_to):
        if nodespace_from_uid is None:
            nodespace_from_uid = self.get_nodespace(None).uid
        if nodespace_to_uid is None:
            nodespace_to_uid = self.get_nodespace(None).uid
        section_from = self.get_section(nodespace_from_uid)
        section_to = self.get_section(nodespace_to_uid)

        if section_to != section_from:
            raise ValueError("Links between sections aren't supported yet, but will be.")

        return section_from.get_link_weights(nodespace_from_uid, group_from, nodespace_to_uid, group_to)

    def set_link_weights(self, nodespace_from_uid, group_from, nodespace_to_uid, group_to, new_w):
        if nodespace_from_uid is None:
            nodespace_from_uid = self.get_nodespace(None).uid
        if nodespace_to_uid is None:
            nodespace_to_uid = self.get_nodespace(None).uid

        section_from = self.get_section(nodespace_from_uid)
        section_to = self.get_section(nodespace_to_uid)

        if section_to != section_from:
            raise ValueError("Links between sections aren't supported yet, but will be.")

        section_from.set_link_weights(nodespace_from_uid, group_from, nodespace_to_uid, group_to, new_w)

        uids_to_invalidate = self.get_node_uids(nodespace_from_uid, group_from)
        uids_to_invalidate.extend(self.get_node_uids(nodespace_to_uid, group_to))

        for uid in uids_to_invalidate:
            if uid in self.proxycache:
                del self.proxycache[uid]

    def get_available_gatefunctions(self):
        return ["identity", "absolute", "sigmoid", "tanh", "rect", "one_over_x"]